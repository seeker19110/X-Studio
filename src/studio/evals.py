"""Eval prompt: mỗi agent có `evals/<agent>.yaml` gồm các ca đầu vào + tiêu chí chấm xác định.
Chạy với model đã cấu hình (`python -m studio.evals script-writer`) hoặc client giả trong test. Không gắn provider nào.

Tiêu chí (`expect`): equals {field: value} · contains {field: substring} · min_len {field: n} · max_len {field: n}
· one_of {field: [v1, v2]}. Ca có thể kèm `extra` (artifact liên quan, như orchestrator enrich) và `context` (blackboard).

Ghi / phát lại: `--record` chạy model thật và lưu `evals/recordings/<agent>.json` khoá bằng hash(system + user);
`--replay` chạy từ bản ghi, không cần model — CI dùng chế độ này. Sửa prompt/skill → hash đổi → CI đỏ cho tới khi
ghi lại bằng model thật (cổng "đổi prompt phải chạy eval" được máy cưỡng chế).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .blackboard import Blackboard
from .bus import InMemoryBus
from .events import Envelope
from .llm import Completion, LLMError, ModelClient
from .registry import load_agents
from .runner import AgentRunner, RunnerError, RunResult

EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
RECORDINGS_DIR = EVALS_DIR / "recordings"


def prompt_key(system: str, user: str) -> str:
    return hashlib.sha256(json.dumps([system, user], ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


def recording_path(agent_id: str) -> Path:
    return RECORDINGS_DIR / f"{agent_id}.json"


def load_recording(agent_id: str) -> dict[str, Any] | None:
    p = recording_path(agent_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


class RecordingClient:
    def __init__(self, inner: ModelClient, agent_id: str):
        self.inner, self.agent_id = inner, agent_id
        self.entries: dict[str, dict[str, Any]] = {}

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion:
        c = self.inner.complete(system=system, user=user, schema=schema, model_tier=model_tier, cache_key=cache_key)
        self.entries[prompt_key(system, user)] = {"text": c.text, "model": c.model, "input_tokens": c.input_tokens,
                                                  "output_tokens": c.output_tokens}
        return c

    def save(self) -> Path:
        spec = load_agents()[self.agent_id]
        data = {"agent": self.agent_id, "prompt_version": spec.version, "recorded_at": datetime.now(UTC).isoformat(),
                "models": sorted({e["model"] for e in self.entries.values()}), "cases": self.entries}
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        p = recording_path(self.agent_id)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return p


class ReplayClient:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.data = load_recording(agent_id)
        if self.data is None:
            raise LLMError(f"chưa có bản ghi eval cho {agent_id}: chạy `make eval-record AGENT={agent_id}` với model thật")

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion:
        e = self.data["cases"].get(prompt_key(system, user))
        if e is None:
            raise LLMError(f"bản ghi eval của {self.agent_id} lệch prompt hiện tại: chạy `make eval-record AGENT={self.agent_id}` "
                           "với model thật rồi commit bản ghi")
        return Completion(text=e["text"], input_tokens=int(e.get("input_tokens", 0)),
                          output_tokens=int(e.get("output_tokens", 0)), model=f"replay:{e.get('model', '?')}")


class _Probe:
    key: str | None = None

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion:
        self.key = prompt_key(system, user)
        raise LLMError("probe")


def stale_recordings(agent_ids: list[str] | None = None) -> dict[str, list[str]]:
    agents = load_agents(); out: dict[str, list[str]] = {}
    for aid in agent_ids or sorted(agents):
        rec = load_recording(aid)
        if rec is None: continue
        missing = []
        for case in load_cases(aid):
            bus = InMemoryBus(); bb = Blackboard(bus)
            for ctx in case.get("context", []):
                bb.write(ctx["actor"], ctx["namespace"], ctx["content_ref"], ctx.get("summary", ""))
            probe = _Probe()
            try: _run_case(aid, case, probe, agents, bb, bus)
            except (RunnerError, LLMError): pass
            if probe.key is not None and probe.key not in rec["cases"]: missing.append(case["name"])
        if missing: out[aid] = missing
    return out


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    tokens: int = 0
    error: bool = False  # không chạy được (bản ghi lệch/thiếu, model lỗi, đầu ra sai schema) — khác với chấm không đạt


def _get(d: Any, dotted: str) -> Any:
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, list) and part.isdigit(): cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict): cur = cur.get(part)
        else: return None
        if cur is None: return None
    return cur


def check(payload: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    for f, v in (expect.get("equals") or {}).items():
        if _get(payload, f) != v: fails.append(f"{f} == {v!r}, thực tế {_get(payload, f)!r}")
    for f, v in (expect.get("contains") or {}).items():
        if str(v).lower() not in str(_get(payload, f) or "").lower(): fails.append(f"{f} phải chứa {v!r}")
    for f, n in (expect.get("min_len") or {}).items():
        if len(_get(payload, f) or []) < n: fails.append(f"len({f}) ≥ {n}")
    for f, n in (expect.get("max_len") or {}).items():
        if len(_get(payload, f) or []) > n: fails.append(f"len({f}) ≤ {n}, thực tế {len(_get(payload, f) or [])}")
    for f, vs in (expect.get("one_of") or {}).items():
        if _get(payload, f) not in vs: fails.append(f"{f} ∈ {vs}")
    return fails


def load_cases(agent_id: str) -> list[dict[str, Any]]:
    p = EVALS_DIR / f"{agent_id}.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("cases", []) if p.exists() else []


def _run_case(agent_id: str, case: dict[str, Any], client: ModelClient, agents: dict | None, bb: Blackboard, bus: InMemoryBus):
    runner = AgentRunner(bus, client, agents, blackboard=bb)
    i = case["input"]
    inp = Envelope(topic=i["topic"], key=i["key"], actor=i.get("actor", "human"), payload=i["payload"])
    if not case.get("many"):
        return runner.run(agent_id, inp, case["topic_out"], extra=case.get("extra"))
    # agent sinh nhiều payload một lượt (channel-strategist, community-manager): chấm phần tử đầu, `min_len: {items}` qua metrics
    g = runner.generate(agent_id, inp, case["topic_out"], many=True, extra=case.get("extra"))
    first = {**g.payloads[0], "_items": len(g.payloads)} if g.payloads else {"_items": 0}
    if g.payloads:
        runner.publish(agent_id, inp, case["topic_out"], g.payloads[0], tokens=g.tokens, model=g.model,
                       context_writes=g.context_writes)
    env = Envelope(topic=case["topic_out"], key=inp.key, actor=agent_id, payload=first)
    return RunResult(output=env, tokens=g.tokens, model=g.model)


def run_eval(agent_id: str, client: ModelClient, agents: dict | None = None) -> list[CaseResult]:
    results = []
    for case in load_cases(agent_id):
        bus = InMemoryBus(); bb = Blackboard(bus)
        for ctx in case.get("context", []):
            bb.write(ctx["actor"], ctx["namespace"], ctx["content_ref"], ctx.get("summary", ""))
        try:
            r = _run_case(agent_id, case, client, agents, bb, bus)
        except (RunnerError, LLMError) as e:
            results.append(CaseResult(case["name"], False, [str(e)], error=True)); continue
        fails = check(r.output.payload, case.get("expect", {}))
        results.append(CaseResult(case["name"], not fails, fails, r.tokens))
    return results


def _print(agent_id: str, res: list[CaseResult]) -> bool:
    for r in res:
        print(f"{'PASS' if r.passed else 'FAIL'} {agent_id}/{r.name} ({r.tokens} tok)" + "".join(f"\n   - {f}" for f in r.failures))
    print(f"{agent_id}: {sum(r.passed for r in res)}/{len(res)} pass")
    return all(r.passed for r in res)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chạy eval prompt của agent: model thật, ghi lại (--record) hoặc phát lại (--replay)")
    ap.add_argument("agent", help="id agent, hoặc `all`")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true")
    mode.add_argument("--replay", action="store_true")
    ap.add_argument("--strict", action="store_true", help="với --replay: agent chưa có bản ghi cũng tính là fail")
    ns = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    agents = load_agents()
    ids = sorted(agents) if ns.agent == "all" else [ns.agent]
    ok = True
    for aid in ids:
        if not load_cases(aid): continue
        if ns.replay:
            try: client: ModelClient = ReplayClient(aid)
            except LLMError as e:
                print(f"SKIP {aid}: {e}"); ok = ok and not ns.strict; continue
        else:
            from .llm import make_client
            client = RecordingClient(make_client(), aid) if ns.record else make_client()
        res = run_eval(aid, client, agents)
        passed = _print(aid, res)
        # --replay (CI): cổng là "bản ghi còn khớp prompt và đầu ra hợp lệ" — ca chấm không đạt là tín hiệu chất lượng
        # cho vòng sau, không làm CI đỏ (đỏ khi bản ghi lệch/thiếu, hoặc --strict). Model thật: mọi ca phải đạt.
        ok = ok and (passed if (ns.strict or not ns.replay) else not any(r.error for r in res))
        if ns.record and isinstance(client, RecordingClient):
            print(f"đã ghi {client.save()}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
