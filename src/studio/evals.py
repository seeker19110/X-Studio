"""Eval prompt: mỗi agent có `evals/<agent>.yaml` gồm các ca đầu vào + tiêu chí chấm xác định.
Chạy với model đã cấu hình (`python -m studio.evals script-writer`) hoặc client giả trong test. Không gắn provider nào.

Tiêu chí (`expect`): equals {field: value} · contains {field: substring} · min_len {field: n} · max_len {field: n}
· one_of {field: [v1, v2]}. Ca có thể kèm `extra` (artifact liên quan, như orchestrator enrich) và `context` (blackboard).

Ghi / phát lại: `--record` chạy model thật và lưu `evals/recordings/<agent>.json` khoá bằng hash(system + user);
`--replay` chạy từ bản ghi, không cần model — CI dùng chế độ này. Sửa prompt/skill → hash đổi → CI đỏ cho tới khi
ghi lại bằng model thật (cổng "đổi prompt phải chạy eval" được máy cưỡng chế).

Agent có tool (ADR-0007): khoá vẫn là hash(system, user lượt đầu); `RecordingClient` chỉ lưu câu trả lời CUỐI (lượt
không gọi tool), `ReplayClient` trả câu trả lời cuối ngay lượt đầu và bỏ qua `tools`/`messages` — phát lại không
gọi tool, không gọi mạng.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xagents_core.evals import CaseResult as CaseResult
from xagents_core.evals import EvalSuite
from xagents_core.evals import RecordingClient as CoreRecordingClient
from xagents_core.evals import ReplayClient as CoreReplayClient
from xagents_core.evals import Threshold as Threshold
from xagents_core.evals import _get as _core_get
from xagents_core.evals import _Probe as _Probe
from xagents_core.evals import check as check
from xagents_core.evals import check_thresholds as check_thresholds
from xagents_core.evals import load_thresholds as _core_load_thresholds
from xagents_core.evals import prompt_key as prompt_key

from .blackboard import Blackboard
from .bus import InMemoryBus
from .core import CORE
from .events import Envelope
from .llm import LLMError, ModelClient
from .registry import load_agents
from .runner import AgentRunner, RunnerError, RunResult

EVALS_DIR = CORE.root / "evals"
RECORDINGS_DIR = EVALS_DIR / "recordings"
REQUIRED_NAME = "REQUIRED.txt"  # agent BẮT BUỘC có bản ghi tươi; thiếu hoặc lệch phiên bản prompt → CI đỏ
DEFAULT_THRESHOLDS_PATH = EVALS_DIR / "thresholds.yaml"  # sàn ĐIỂM CHẤM (p3.3), khác REQUIRED.txt gác bản ghi


class Suite(EvalSuite):
    """Bộ eval của studio. Chỉ khai thứ core không được biết; phần còn lại ở `xagents_core.evals`."""

    case_errors = (RunnerError, LLMError)

    # Đọc từ BIẾN MODULE, không từ `self.root`: `monkeypatch.setattr(ev, "RECORDINGS_DIR", …)` là seam có sẵn
    # của nhiều ca test — tính từ `self.root` là seam ấy im lặng hết tác dụng (xem chú cùng chỗ ở company).
    @property
    def evals_dir(self) -> Path: return EVALS_DIR

    @property
    def recordings_dir(self) -> Path: return RECORDINGS_DIR

    def load_cases(self, agent_id: str) -> list[dict[str, Any]]:
        # Qua HÀM MODULE: `monkeypatch.setattr(evals, "load_cases", …)` là seam của nhiều ca test.
        # Hàm module gọi thẳng bản cơ sở `EvalSuite.load_cases` nên không có đệ quy.
        return load_cases(agent_id)

    def load_agents(self) -> dict[str, Any]:
        return load_agents()

    def new_bus(self) -> InMemoryBus:
        return InMemoryBus()

    def new_blackboard(self, bus: InMemoryBus) -> Blackboard:
        return Blackboard(bus)

    def run_case(self, agent_id: str, case: dict[str, Any], client: ModelClient,
                 agents: dict[str, Any] | None, bb: Blackboard, bus: InMemoryBus) -> Any:
        # Gọi HÀM MODULE, không viết thân ở đây: `monkeypatch.setattr(evals, "_run_case", spy)` là seam có sẵn
        # của nhiều ca test. Viết thân trong phương thức là seam ấy im lặng hết tác dụng — ca vẫn xanh mà spy
        # không bao giờ chạy, tức nó thôi đo cái nó sinh ra để đo.
        return _run_case(agent_id, case, client, agents, bb, bus)

SUITE = Suite(CORE.root)


# Tên cũ, chữ ký cũ — mọi nơi gọi và mọi test giữ nguyên; cơ chế ở core.
def recording_path(agent_id: str) -> Path: return SUITE.recording_path(agent_id)
def load_recording(agent_id: str) -> dict[str, Any] | None: return SUITE.load_recording(agent_id)
def load_cases(agent_id: str) -> list[dict[str, Any]]: return EvalSuite.load_cases(SUITE, agent_id)
def required_agents() -> list[str]: return SUITE.required_agents()
def outdated_versions(ids: list[str] | None = None) -> dict[str, str]: return SUITE.outdated_versions(ids)
def stale_recordings(ids: list[str] | None = None) -> dict[str, list[str]]: return SUITE.stale_recordings(ids)
def _lines(agent_id: str, res: list[CaseResult]) -> list[str]: return SUITE.lines(agent_id, res)
def _get(d: Any, dotted: str) -> Any: return _core_get(d, dotted)


def run_eval(agent_id: str, client: ModelClient, agents: dict[str, Any] | None = None) -> list[CaseResult]:
    return SUITE.run_eval(agent_id, client, agents)


class RecordingClient(CoreRecordingClient):
    def __init__(self, inner: ModelClient, agent_id: str):
        super().__init__(inner, agent_id, SUITE)


class ReplayClient(CoreReplayClient):
    def __init__(self, agent_id: str):
        super().__init__(agent_id, SUITE)


def load_thresholds(path: Path | None = None) -> dict[str, Threshold]:
    """Đường dẫn mặc định là NGHĨA của studio; cơ chế đọc/kiểm hình ở `xagents_core.evals` (p3.3)."""
    return _core_load_thresholds(path or DEFAULT_THRESHOLDS_PATH)


@dataclass(frozen=True)
class _AgentOutcome:
    """Ba trường `check_thresholds` cần (`ScoredOutcome` của core). Studio KHÔNG có `gate_ok`/`cases_ok` như
    company: cổng ở đây là một cờ duy nhất trong `main` — chính sách khác nhau nên không gộp (ADR-0001)."""
    agent_id: str
    res: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.res)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.res)


def _run_case(agent_id: str, case: dict[str, Any], client: ModelClient, agents: dict[str, Any] | None,
              bb: Blackboard, bus: InMemoryBus) -> Any:
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chạy eval prompt của agent: model thật, ghi lại (--record) hoặc phát lại (--replay)")
    ap.add_argument("agent", help="id agent, hoặc `all`")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true")
    mode.add_argument("--replay", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="với --replay: agent trong evals/recordings/REQUIRED.txt mà thiếu bản ghi hoặc bản ghi lệch "
                         "phiên bản prompt thì tính là fail")
    ap.add_argument("--thresholds", type=Path, default=None, metavar="PATH",
                    help="ngưỡng điểm eval theo agent (p3.3), mặc định evals/thresholds.yaml nếu tồn tại; "
                         "agent tụt dưới sàn làm CI đỏ")
    ap.add_argument("--no-thresholds", action="store_true", help="tắt cổng ngưỡng điểm eval, giữ hành vi cũ")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="chạy N agent song song (K5.3). Mỗi agent một client và một file bản ghi riêng nên "
                         "không tranh nhau; thứ tự IN vẫn theo id. Song song ở đây là chờ MẠNG, không phải CPU")
    ns = ap.parse_args(argv)
    if ns.jobs < 1: ap.error("--jobs phải >= 1")
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    agents = load_agents()
    ids = sorted(agents) if ns.agent == "all" else [ns.agent]
    ok = True
    required = set(required_agents()) if ns.strict else set()
    th: dict[str, Threshold] = {}
    if not ns.no_thresholds:
        th_path = ns.thresholds if ns.thresholds is not None else DEFAULT_THRESHOLDS_PATH
        if ns.thresholds is not None or th_path.exists():
            th = load_thresholds(th_path)
    if ns.strict:
        for aid, why in outdated_versions(ids).items():
            print(f"FAIL {aid}: {why} — chạy `make eval-record AGENT={aid}` rồi commit lại"); ok = False
    def _one(aid: str) -> tuple[_AgentOutcome, list[str], bool]:
        """Một agent, chạy độc lập được (K5.3): client riêng, file bản ghi riêng, `save()` gộp chứ không ghi đè.
        Dòng in được GOM lại thay vì `print` thẳng — với `--jobs > 1`, in thẳng là log cài răng lược."""
        lines: list[str] = []
        if not load_cases(aid): return _AgentOutcome(aid), lines, True
        if ns.replay:
            try: client: ModelClient = ReplayClient(aid)
            except LLMError as e:
                lines.append(f"{'FAIL' if aid in required else 'SKIP'} {aid}: {e}")
                return _AgentOutcome(aid), lines, aid not in required
        else:
            from .llm import make_client
            client = RecordingClient(make_client(), aid) if ns.record else make_client()
        res = run_eval(aid, client, agents)
        lines += _lines(aid, res)
        if ns.record and isinstance(client, RecordingClient):
            lines.append(f"đã ghi {client.save()}")
        # --replay (CI): cổng là "bản ghi còn khớp prompt và đầu ra hợp lệ" — ca chấm không đạt là tín hiệu chất lượng
        # cho vòng sau, không làm CI đỏ (đỏ khi bản ghi lệch/thiếu prompt, hoặc --strict + REQUIRED.txt). Model thật: mọi ca phải đạt.
        passed = all(r.passed for r in res)
        return _AgentOutcome(aid, res), lines, (passed if not ns.replay else not any(r.errored for r in res))

    # Thứ tự IN theo id kể cả khi chạy song song: log so được giữa hai lần chạy.
    if ns.jobs > 1 and len(ids) > 1:
        with ThreadPoolExecutor(max_workers=ns.jobs) as pool:
            rows = list(pool.map(_one, ids))
    else:
        rows = [_one(aid) for aid in ids]
    for _o, lines, agent_ok in rows:
        for ln in lines: print(ln)
        ok = ok and agent_ok
    # Cổng ĐIỂM CHẤM (p3.3): trước đây `--replay` chỉ gác `errored`, nên ca chạy được mà chấm SAI NỘI DUNG
    # vẫn xanh. Ngưỡng tính SAU khi gom xong mọi outcome nên dùng được cả với `--jobs > 1`.
    if th:
        for line in check_thresholds([o for o, _l, _k in rows], th):
            print(line); ok = False
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
