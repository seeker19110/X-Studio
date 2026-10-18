"""Run a small real-provider project in an isolated factory and stop at its publish gate."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

from studio.events import Envelope
from studio.factory import Factory
from studio.media import load_media_config
from studio.orchestrator import _with_provenance
from studio.policy_receipts import ACTOR, sign


def prepare_rights(factory: Factory, root: Path, dossier: Path, trigger: Envelope) -> dict:
    """Have the actual namespace owner record scoped operator evidence, without a fabricated review."""
    raw = dossier.read_bytes()
    declaration = json.loads(raw)
    if declaration.get("scope") != "private_local_validation":
        raise ValueError("This smoke only accepts a private local validation rights dossier")
    checksum = hashlib.sha256(raw).hexdigest()
    target = root / f"rights-dossier-{checksum[:16]}.json"
    extra = _with_provenance(trigger, factory.orch)
    if extra.get("operator_rights_dossier", {}).get("declaration") != declaration:
        raise ValueError("The dossier must match this project operator request")
    target.write_bytes(raw)
    extra["dossier_ref"] = str(target)
    generated = factory.orch.runner.run_context(
        "rights-checker",
        trigger,
        extra=extra,
    )
    context = factory.orch.blackboard.read("rights")
    if context is None or not context.summary.strip():
        raise ValueError("Rights intake did not retain a record; inspect the model response")
    receipt = {
        "sha256": checksum,
        "dossier": str(target),
        "declaration": declaration,
        "source_event_id": trigger.event_id,
        "video_id": trigger.payload["video_id"],
        "source_database": str(root / "studio.sqlite"),
        "context": context.model_dump(mode="json"),
        "generation": dataclasses.asdict(generated),
    }
    (root / ("rights-intake-" + trigger.event_id + ".json")).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"rights_intake": str(target), "model": generated.model, "tokens": generated.tokens}), flush=True)
    return receipt


def authorize_local_plan(factory: Factory, root: Path, declaration: dict, *, allowed_risks: frozenset[str] = frozenset({'brand'})) -> None:
    """Explicit CLI opt-in signs one scoped plan, never a publication or human signature."""
    for gate in list(factory.orch.gate.pending.values()):
        if gate.kind != "plan":
            continue
        plan = factory.orch.plans[gate.subject_id]
        if (
            declaration.get("scope") != "private_local_validation"
            or len(plan["briefs"]) != 1
            or any(set(b.get("risk_tags", [])) - allowed_risks for b in plan["briefs"])
            or factory.orch.desk.check_plan(plan["briefs"])
        ):
            raise ValueError("The requested plan exceeds this local naming authorization")
        receipt = sign(
            {
                "subject_id": gate.subject_id,
                "decision": "approve",
                "by": ACTOR,
                "policy": "production",
                "request_at": gate.created_at.isoformat(),
                "reason": "Operator explicitly authorized this local validation with the supplied rights dossier and token budget; independent media reviews remain required. No publication approval.",
            }
        )
        factory.bus.publish(
            Envelope(
                topic="audit-log",
                key=ACTOR,
                actor=ACTOR,
                payload={"actor": ACTOR, "action": "gate.decide", "evidence": json.dumps(receipt, ensure_ascii=False)},
            )
        )
        (root / "plan-authorization.json").write_text(
            json.dumps(
                {
                    "receipt": receipt,
                    "scope": "This single local production plan, no publication approval",
                    "plan": plan,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-tokens", type=int, default=500000)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--max-seconds", type=int, default=2700)
    parser.add_argument("--rights-dossier", type=Path)
    parser.add_argument(
        "--authorize-local-plan",
        action="store_true",
        help="Delegate this private test plan, including its own project name; never approve publication",
    )
    args = parser.parse_args()
    if args.authorize_local_plan and not args.rights_dossier:
        parser.error("--authorize-local-plan requires the scoped rights dossier")
    root = args.resume or Path("output/real-validation") / datetime.now().strftime("factory-%Y%m%d-%H%M%S")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=bool(args.resume))
    os.environ["STUDIO_POLICY_KEY_FILE"] = str(root / "policy.key")
    cfg = copy.deepcopy(load_media_config())
    cfg.output_dir = root / "media"
    cfg.upload_dir = root / "uploads"
    cfg.tts = {"provider": "edge", "voice": "vi-VN-HoaiMyNeural"}
    cfg.image["timeout_s"] = 180
    cfg.video.update(resolution="640x360", fps=25, motion="none", transition_s=0, burn_captions=True)
    cfg.render.update(threads=2, mode="bounded")
    cfg.production.update(automation="production", max_minutes=1)
    factory = Factory(root / "studio.sqlite", cfg, demo=False)
    clients = getattr(factory.orch.runner.client, "backends", [])
    for backend in clients.values() if isinstance(clients, dict) else clients:
        if hasattr(backend.client, "timeout"):
            backend.client.timeout = 120.0
    try:
        declaration = json.loads(args.rights_dossier.read_text(encoding="utf-8")) if args.rights_dossier else {}
        request = {
            "title": "Trước khi sửa video: kiểm tra, sao lưu rồi xem lại",
            "audience": "Người mới dùng X Studio",
            "target_minutes": 0.5,
            "format": "long",
            "language": "vi",
            "budget_tokens": args.budget_tokens,
            "rights_dossier": declaration,
            "boundaries": [
                "Chỉ một video khoảng 30 giây, tối đa hai cảnh; tổng khoảng 55 đến 65 từ tiếng Việt, đã bao gồm hook và CTA.",
                "Nội dung chỉ gồm: kiểm tra môi trường; sao lưu vào thư mục mới; sửa một cảnh; xem bản cuối và phụ đề rồi mới duyệt.",
                "Đây là video kiểm thử nội bộ, không quảng cáo hoặc nêu số liệu; không đăng công khai.",
                "Không dùng stock hoặc tài nguyên bên ngoài. Hình vector do provider đã cấu hình tạo.",
                "Đọc đầy đủ hook đã chọn và CTA trong narration của manifest. Tiêu đề và thumbnail chỉ hứa đúng các bước được đọc trong video.",
                "Tiêu đề và chữ thumbnail chỉ nói quy trình kiểm tra–sao lưu–xem lại, không gắn tên phần mềm hoặc hứa demo UI. Tên dự án nằm trong nhãn bài thử ở lời đọc.",
                "Lời đọc nhắc cụm 'bài thử nội bộ X Studio' đúng một lần, chỉ để định danh bài thử; không hứa quay màn hình hay demo giao diện X Studio. Hình là sơ đồ minh họa chung, không có logo hoặc thương hiệu ngoài. Hồ sơ quyền của người vận hành đi kèm yêu cầu.",
                "Nội dung là lời hướng dẫn thao tác, không khẳng định tính năng tự động, tốc độ hay kết quả đã đo của phần mềm; không cần đưa số liệu hoặc xu hướng bên ngoài vào kịch bản.",
                "Metadata description không chép lại timestamp chương; dùng trường chapters để code căn mốc từ timeline thật.",
            ],
        }
        command = "resume" if args.resume else factory.command("create", request)
        print(json.dumps({"root": str(root), "command": command}), flush=True)
        (Path("output/real-validation") / "live-factory-path.txt").write_text(str(root), encoding="utf-8")
        started = time.monotonic()
        previous = None
        while time.monotonic() - started < args.max_seconds:
            if args.authorize_local_plan:
                authorize_local_plan(factory, root, declaration)
            if args.rights_dossier:
                for final in list(factory.bus.replay("media-assets")):
                    if (
                        final.payload["kind"] == "final_video"
                        and not (root / ("rights-intake-" + final.event_id + ".json")).exists()
                    ):
                        prepare_rights(factory, root, args.rights_dossier, final)
            factory.cycle()
            state = factory.snapshot()
            progress = {
                "projects": [(p["id"], p["state"], p["manifest_version"]) for p in state["projects"]],
                "gates": [(g["id"], g["kind"]) for g in state["gates"]],
                "queue": state["queue"],
                "deferred": state["deferred"],
                "events": [e["action"] for e in state["events"][:3]],
            }
            if progress != previous:
                print(json.dumps(progress, ensure_ascii=False), flush=True)
                previous = progress
            (root / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            if any(g["kind"] == "publish" for g in state["gates"]) and not any(
                g["kind"] == "escalation" for g in state["gates"]
            ):
                assert all(
                    p["reviews"].get(k, {}).get("verdict") == "pass"
                    for p in state["projects"]
                    for k in ("fact", "rights", "quality")
                )
                assert not list(factory.bus.replay("publish-events")), "This smoke must stop before publishing"
                assert not any(a["provider"] == "fake" for p in state["projects"] for a in p["media"])
                assert all(
                    factory.orch.supervisor.budgets[p["id"]].used <= factory.orch.supervisor.budgets[p["id"]].limit
                    for p in state["projects"]
                )
                (root / "result.json").write_text(
                    json.dumps(
                        {"status": "publish_gate_reached", "elapsed_s": time.monotonic() - started, "state": state},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print("REAL PROJECT READY; publish gate left pending", flush=True)
                return
            if any(g["kind"] == "escalation" for g in state["gates"]):
                break
            time.sleep(0.2)
        (root / "result.json").write_text(
            json.dumps(
                {"status": "needs_attention", "elapsed_s": time.monotonic() - started, "state": factory.snapshot()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise SystemExit("Real factory stopped for inspection; see isolated state.json")
    finally:
        factory.close()


if __name__ == "__main__":
    main()
