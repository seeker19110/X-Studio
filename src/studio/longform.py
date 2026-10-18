"""Bounded LLM stages for long videos, with per-chapter restart checkpoints.

These are data partitions of the existing agents, not new agents or executable
prompts. Script claims stay attached to sections; review verdicts merge conservatively.
"""
from __future__ import annotations

import copy
import json
import math
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .bus import BusError
from .events import Envelope
from .render_cache import fingerprint
from .runner import AgentRunner, Generated, RunnerError


def _chapter_narration_matches(generated: Generated, script: dict[str, Any]) -> bool:
    expected = ' '.join(s['narration'] for s in script['sections']).split()
    actual = ' '.join(s['narration'] for p in generated.payloads for s in p['scenes']).split()
    return actual == expected


def generate_partitioned(runner: AgentRunner, agent: str, inp: Envelope, topic: str,
                         extra: dict[str, Any], root: Path, *, chapter_minutes: float = 8,
                         many: bool = False) -> Generated:
    if chapter_minutes <= 0: raise RunnerError("chapter_minutes must be positive")
    brief = extra.get("brief", inp.payload)
    minutes = float(brief.get("target_minutes", 0))
    script = extra.get("script", inp.payload if inp.topic == "scripts" else {})
    parts: list[tuple[dict, dict]] = []
    if agent == "script-writer" and minutes > chapter_minutes:
        count = math.ceil(minutes / chapter_minutes)
        for index in range(count):
            payload, context = copy.deepcopy(inp.payload), copy.deepcopy(extra)
            target = context.get("brief", payload)
            target["target_minutes"] = minutes / count
            context["chapter"] = {"index": index + 1, "total": count, "target_minutes": minutes / count,
                                  "outline": brief.get("key_points", []),
                                  "instruction": "Write only this chapter. Keep continuity; no repeated introduction or ending."}
            parts.append((payload, context))
    elif agent in {"production-manager", "fact-checker"} and len(script.get("sections", [])) > 6:
        sections = script["sections"]
        for index in range(0, len(sections), 4):
            payload, context = copy.deepcopy(inp.payload), copy.deepcopy(extra)
            subset = copy.deepcopy(script); subset["sections"] = sections[index:index+4]
            ids = {cid for s in subset["sections"] for cid in s.get("claim_ids", [])}
            subset["claims"] = [c for c in script.get("claims", []) if c["claim_id"] in ids]
            # The first/last chapter owns intro/outro. Claims without section references are still reviewed once.
            if index == 0:
                referenced = {cid for s in sections for cid in s.get("claim_ids", [])}
                subset["claims"] += [c for c in script.get("claims", []) if c["claim_id"] not in referenced]
            if index: subset["hook"] = ""
            if index + 4 < len(sections): subset["cta"] = ""
            if inp.topic == "scripts": payload = subset
            else: context["script"] = subset
            context["chapter"] = {"index": index//4+1, "total": math.ceil(len(sections)/4),
                                  "instruction": "Process only the supplied chapter sections, in order; preserve narration and claim references. Do not repeat sections from a complete reference script elsewhere in the brief."}
            parts.append((payload, context))
    if not parts:
        return runner.generate(agent, inp, topic, many=many, extra=extra or None)
    # Script payloads do not carry the project's budget. Resolve the durable brief
    # before starting more paid chapter calls, including partitioned fact reviews.
    if "budget_tokens" not in brief:
        stored = list(runner.bus.replay("video-briefs", brief.get("video_id") or inp.key))
        if stored: brief = {**stored[-1].payload, **brief}
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []; writes: list[dict] = []; models: set[str] = set()
    spent = sum(e.payload.get("tokens", 0) for e in runner.bus.replay("audit-log")
                if e.payload.get("video_id") == brief.get("video_id"))
    budget = int(brief.get("budget_tokens", 0))
    config = getattr(runner.client, "cfg", None)
    identity = asdict(config) if is_dataclass(config) and not isinstance(config, type) else type(runner.client).__qualname__
    for index, (payload, context) in enumerate(parts):
        chapter_script = payload if inp.topic == 'scripts' else context.get('script', {})
        key = fingerprint({"format": 1, "agent": agent, "version": runner.agents[agent].version,
                           "prompt": runner.agents[agent].system_prompt(), "client": identity,
                           "payload": payload, "extra": context, "topic": topic})
        checkpoint = root / f"{key}.json"
        try:
            generated = Generated(**json.loads(checkpoint.read_text(encoding="utf-8")))
            for result in generated.payloads: runner.bus.validate(topic, result)
            if agent == 'production-manager' and not _chapter_narration_matches(generated, chapter_script):
                raise ValueError('cached chapter narration does not match its script scope')
        except (OSError, ValueError, KeyError, TypeError, BusError):
            if budget and spent >= budget: raise RunnerError(f"video budget exhausted: {spent}/{budget} tokens") from None
            generated = runner.generate(agent, inp.model_copy(update={"payload": payload}), topic, extra=context)
            spent += generated.tokens
            # Charge immediately; a later chapter failing must not hide earlier paid work from the supervisor.
            runner._audit(runner.agents[agent], "chapter.completed", inp, tokens=generated.tokens,
                          evidence=json.dumps({"chapter": index+1, "total": len(parts), "model": generated.model}))
            if agent == 'production-manager' and not _chapter_narration_matches(generated, chapter_script):
                raise RunnerError('chapter narration must match only its supplied script sections, in order') from None
            with tempfile.TemporaryDirectory(dir=root) as temp:
                staged = Path(temp) / "result.json"
                staged.write_text(json.dumps(asdict(generated), ensure_ascii=False), encoding="utf-8")
                staged.replace(checkpoint)
        if len(generated.payloads) != 1: raise RunnerError("chapter must return exactly one artifact")
        results.append(copy.deepcopy(generated.payloads[0])); writes.extend(generated.context_writes); models.add(generated.model)
    merged = results[0]
    if topic == "scripts":
        sections, claims = [], []
        for index, result in enumerate(results):
            prefix = f"C{index+1}_"
            for claim in result.get("claims", []): claim["claim_id"] = prefix + claim["claim_id"]
            for section in result["sections"]:
                section["claim_ids"] = [prefix + c for c in section.get("claim_ids", [])]
            sections.extend(result["sections"]); claims.extend(result.get("claims", []))
        merged.update(sections=sections, claims=claims, cta=results[-1].get("cta", ""))
        words = sum(len(s["narration"].split()) for s in sections)
        merged.update(word_count=words, estimated_minutes=round(words/150, 2))
    elif topic == "scene-manifests":
        scenes: list[dict[str, Any]] = []
        for index, result in enumerate(results):
            for scene in result["scenes"]:
                scene.update(scene_id=f"C{index+1}_S{len(scenes)+1}", order=len(scenes)); scenes.append(scene)
        merged["scenes"] = scenes
    elif topic == "review-results":
        rank = {"pass": 0, "fail": 1, "block": 2}
        merged["verdict"] = max((r["verdict"] for r in results), key=lambda v: rank.get(v, 2))
        merged["findings"] = [f for r in results for f in r.get("findings", [])]
    runner.bus.validate(topic, merged)
    # Tokens already audited at each checkpoint, including work recovered after restart.
    return Generated(payloads=[merged], tokens=0, model=" + ".join(sorted(models)), context_writes=writes)
