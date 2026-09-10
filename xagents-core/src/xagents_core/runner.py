"""Khung runner chung của hai công ty (K3.6d1 của ADR gốc 0001).

**Vì sao K3.6d lại tách đôi, và vì sao bước này CHỈ có ngần này.**

Đo theo symbol (bài học K3.6c — `difflib` trên cả file chỉ dùng để xếp thứ tự module):

| symbol | difflib | | symbol | difflib |
|---|---|---|---|---|
| `RunnerError`, `payload_schema` | **1.00** | | `_complete` | 0.32 |
| `run` | 0.91 | | `output_schema` | 0.21 |
| `RunResult` | 0.86 | | `_tool_loop`(+`_turns`) | **0.12** |
| `__init__` | 0.83 | | `write_context` | 0.15 |
| `build_user_message` | 0.74 | | `AgentRunner` (cả lớp) | 0.14 |
| `context_writes_schema` | 0.69 | | | |

`output_schema` 0.21 là một cái bẫy đọc số: hai bản **giống hệt nhau về logic**, lệch chỉ vì company có
docstring còn studio không. Đọc bằng mắt mới thấy; tin con số thì đã tách nhầm.

**Ràng buộc thật của K3.6d không phải độ lệch mã, mà là BẢN GHI EVAL.** Khoá bản ghi là
`hash(system_prompt, user_message)`, và `user_message` do `build_user_message` sinh ra. Đo trực tiếp: thêm
**một dấu cách** vào chuỗi cuối của `build_user_message` studio rồi chạy `python -m studio.evals all --replay`
— mọi ca chuyển thành *"bản ghi eval lệch prompt hiện tại"*. Nghĩa là hợp nhất bất kỳ CHỮ nào trong prompt
(`build_user_message`, `context_writes_schema`, `tools_prompt`) đòi chạy lại `make eval-record` bằng **model
thật** cho cả 20 agent — bảy bước `CONTRIBUTING.md` §3, cần API key. Đó là việc của một PR khác, có người và
có key; không phải của một PR chuyển mã.

Nên bước này giữ **mọi chuỗi prompt nguyên vẹn từng byte ở từng công ty**, và chỉ đưa lên core thứ chứng minh
được là không đụng tới prompt: ba lớp kết quả, và hai hàm schema.

`context_writes_schema` **ở lại từng công ty**, dù `difflib` 0.69 trông như gộp được: company bắt buộc trường
`content` (toàn văn artifact, ADR-0012), studio thì không. Cho studio bản company là đổi hợp đồng đầu ra của
14 agent — tức là đổi prompt. `output_schema` vì thế **nhận** schema ấy làm tham số thay vì tự dựng.

---

**K3.6d2 thêm `AgentRunner` (phần ngoài).** Nguyên tắc của bước này gắt hơn các bước trước: *cơ chế lên core,
**hành vi quan sát được của mỗi công ty giữ nguyên từng byte***. Hai chỗ mà "lấy bản company" sẽ đổi hành vi
studio một cách âm thầm, và cả hai giải được bằng tham số chứ không phải bằng một quyết định:

1. **`write_context` audit `context_no_content`.** Bản company ghi audit này khi `context_writes` thiếu toàn
   văn. `context_writes` của studio **không bao giờ có `content`** (prompt studio không hỏi, xem trên), nên chép
   nguyên bản là **mỗi lần ghi context của studio sinh một audit rác**. → cờ `wants_content`, mặc định `False`.
2. **`publish` dựng envelope.** Company dùng `inp.child(...)` (nối chuỗi nhân quả `correlation_id`/
   `causation_id`), studio dựng `Envelope(...)` mới. Cho studio `child()` là **đổi nội dung event trên bus** —
   nghe như cải tiến, nhưng nó là thay đổi dữ liệu, không phải chuyển mã. → hook `_new_envelope`.

Hai thứ khác cùng loại, cũng thành hook: **trường phạm vi của `AuditLog`** (`ticket_id`/`project_id` vs
`video_id`/`channel_id` — đúng khuôn K3.5a) qua `_audit_scope`, và **câu evidence của `produced:*`** qua
`_produced_evidence` (company dựng JSON từ `Generated.evidence()`, studio dựng một chuỗi ngắn).

`generate` và vòng lặp tool **không** ở đây: chúng gọi `build_user_message`/`tools_prompt`, tức là chạm prompt.
`generate_in_workspace`/`author_tests` của company phụ thuộc `workspace.py`; `_filter_comments` là của studio.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from .bus import BusError
from .events import Envelope
from .llm import ModelClient
from .observe import SpanSink
from .registry import AgentSpec

__all__ = ["AgentRunner", "Generated", "RunResult", "RunnerError", "output_schema", "payload_schema"]

E = TypeVar("E", bound=Envelope)
S = TypeVar("S", bound=AgentSpec)   # `AgentSpec` của công ty (studio thêm `tools`)


class RunnerError(Exception): ...


@dataclass
class RunResult:
    """Một lượt agent đã publish. Company thêm `cost_usd`; studio chưa tính tiền nên nó ở lớp con."""
    output: Any          # `Envelope` của công ty — core không thu hẹp, lớp con thu hẹp (tiền lệ K3.5a)
    tokens: int
    model: str


@dataclass
class Generated:
    """Đầu ra model đã qua kiểm tra schema nhưng CHƯA publish (để code xác định quyết định).

    Bảy trường ở đây là phần CẢ HAI công ty ghi. Company thêm sáu trường của mình (`output_tokens`,
    `cost_usd`, `priced`, `duration_ms`, `phase`) ở lớp con — cùng lý do `AuditLog` ở K3.5a: đưa
    `phase` (ADR-0037) lên core là bắt studio mang một trường nó không bao giờ ghi."""
    payloads: list[dict[str, Any]]
    tokens: int
    model: str
    context_writes: list[dict[str, Any]] = field(default_factory=list)
    cache_hit_ratio: float = 0.0  # phần input lấy từ prompt cache, để đo hiệu quả cache trong audit-log
    turns: int = 1                # số lượt gọi model (1 = không dùng tool)
    tool_calls: dict[str, int] = field(default_factory=dict)  # tên tool → số lần gọi


def payload_schema(schema_dir: Path, topic: str) -> dict[str, Any]:
    """Phần `payload` trong JSON Schema của một topic. Giống hệt nhau hai bên (`difflib` 1.00)."""
    p = schema_dir / f"{topic}.json"
    if not p.exists():
        raise RunnerError(f"không có schema cho topic {topic}")
    got: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))["properties"]["payload"]
    return got


def output_schema(schema: dict[str, Any] | None, namespaces: list[str], many: bool,
                  writes_schema: dict[str, Any]) -> dict[str, Any]:
    """Schema gốc gửi cho model. Agent không sở hữu namespace và chỉ trả một payload: giữ nguyên schema topic.
    Ngược lại bọc thành {"payload"|"items": ..., "context_writes": [...]} (structured output cần object ở gốc).

    `writes_schema` là tham số chứ không dựng tại chỗ: hình dạng `context_writes` là **hợp đồng đầu ra** của
    agent, tức là prompt, tức là của từng công ty (company đòi `content`, studio không) — xem docstring module."""
    if schema is None:  # context-only
        return {"type": "object", "properties": {"context_writes": writes_schema}, "required": ["context_writes"]}
    if not namespaces and not many:
        return schema
    props: dict[str, Any] = {"items": {"type": "array", "items": schema}} if many else {"payload": schema}
    if namespaces: props["context_writes"] = writes_schema
    return {"type": "object", "properties": props, "required": ["items" if many else "payload"]}


class AgentRunner(Generic[E, S]):
    """Phần ngoài của runner: kiểm quyền, publish, ghi blackboard, ghi sổ. `generate` (gọi model, dựng prompt)
    ở lại từng công ty — xem docstring module."""

    #: Lớp con đặt lại. Cùng lý do `envelope_cls` ở bus và `spec_cls` ở registry: dựng bằng lớp core là làm rơi
    #: trường của lớp con im lặng.
    envelope_cls: type[E]
    audit_cls: type[Any]
    generated_cls: type[Any]
    run_result_cls: type[Any]
    #: `context_writes` của công ty này có mang toàn văn (`content`) không — quyết định 1 ở docstring module.
    wants_content: bool = False
    #: `topic_out` đặc biệt: agent chỉ ghi blackboard, không publish topic nào.
    CONTEXT_ONLY = "shared-context"

    def __init__(self, bus: Any, client: ModelClient, agents: dict[str, S],
                 blackboard: Any = None, max_input_chars: int | None = None,
                 default_max_input_chars: int = 0):
        self.bus, self.client = bus, client
        self.agents = agents
        self.blackboard = blackboard
        self.max_input_chars = (max_input_chars or getattr(client, "max_input_chars", None)
                                or default_max_input_chars)
        #: ADR-0009: nơi phát span của runner. Thuộc tính instance chứ không phải tham số dựng, để bật/tắt quan
        #: sát không đổi chữ ký mà hàng chục chỗ trong hai công ty đang gọi. `None` = tắt hoàn toàn (no-op thật).
        self.sink: SpanSink | None = None

    # ---------- hook của từng công ty ----------

    def _audit_scope(self, inp: E) -> dict[str, Any]:
        """Trường PHẠM VI của `AuditLog`: company `ticket_id`/`project_id`, studio `video_id`/`channel_id`.
        Core không được biết tên nào trong số đó (khuôn `AuditLog` ở K3.5a)."""
        return {}

    def _new_envelope(self, inp: E, topic: str, key: str, actor: str, payload: dict[str, Any]) -> E:
        """Dựng envelope đầu ra. Company nối chuỗi nhân quả bằng `inp.child(...)`, studio dựng mới —
        quyết định 2 ở docstring module."""
        return self.envelope_cls(topic=topic, key=key, actor=actor, payload=payload)

    def _produced_evidence(self, g: Any, event_id: str) -> str:
        """Câu `evidence` của audit `produced:*`. Hai công ty ghi hai thứ khác nhau vào sổ."""
        return f"{g.model} event={event_id}"

    def _context_project(self, inp: E) -> str | None:
        """Dự án của một envelope, để phân vùng blackboard (ADR-0018). Studio chưa có khái niệm này."""
        return None

    def _extra_audit_on_publish(self, spec: S, inp: E, out: E, topic_out: str, payload: dict[str, Any]) -> None:
        """Audit thêm mà một công ty ghi lúc publish (company: `ruling` của ADR-0030). Mặc định không có."""

    def generate(self, agent_id: str, inp: E, topic_out: str, *args: Any, **kw: Any) -> Any:
        """Gọi model và kiểm schema. Ở lại từng công ty: nó dựng prompt, mà prompt là khoá bản ghi eval.

        `*args, **kw` để mỗi công ty khai tham số riêng của mình (`many`/`tools`/`phase` ở company,
        `many`/`extra` ở studio) mà không phải nhồi cả hai bộ vào một chữ ký ở core — core không được biết
        `phase` (ADR-0037) hay `extra` là gì."""
        raise NotImplementedError

    # ---------- cơ chế ----------

    def _audit(self, spec: S, action: str, inp: E, evidence: str, tokens: int = 0, **extra: Any) -> None:
        a = self.audit_cls(actor=spec.id, action=action, tokens=tokens, evidence=evidence,
                           **self._audit_scope(inp), **extra)
        self.bus.publish(self.envelope_cls(topic="audit-log", key=spec.id, actor=spec.id, payload=a.model_dump()))

    def run(self, agent_id: str, inp: E, topic_out: str, key: str | None = None, **kw: Any) -> Any:
        g = self.generate(agent_id, inp, topic_out, **kw)
        out = self.publish(agent_id, inp, topic_out, g.payloads[0], key=key, tokens=g.tokens, model=g.model,
                           context_writes=g.context_writes, cache_hit_ratio=g.cache_hit_ratio, generated=g)
        return self._run_result(out, g)

    def _run_result(self, out: E, g: Any) -> Any:
        return self.run_result_cls(output=out, tokens=g.tokens, model=g.model)

    def run_context(self, agent_id: str, inp: E, **kw: Any) -> Any:
        """Lượt chỉ ghi blackboard (company: docs, threat model; studio: chưa dùng)."""
        g = self.generate(agent_id, inp, self.CONTEXT_ONLY, **kw)
        self.write_context(agent_id, inp, g.context_writes)
        self._audit(self.agents[agent_id], "produced:shared-context", inp,
                    evidence=self._produced_evidence(g, ""), tokens=g.tokens, **self._produced_extra(g))
        return g

    def _produced_extra(self, g: Any) -> dict[str, Any]:
        """Trường thêm của audit `produced:*` (company: cost, output_tokens, phase)."""
        return {}

    def write_context(self, agent_id: str, inp: E, writes: list[dict[str, Any]]) -> list[str]:
        """Ghi các artifact lên blackboard dưới danh nghĩa agent; namespace không thuộc agent bị bỏ và ghi audit.

        `wants_content`: chỉ công ty có `content` trong hợp đồng `context_writes` mới ghi toàn văn và mới audit
        `context_no_content` khi thiếu. Bật nó cho công ty không hỏi `content` là sinh một audit rác mỗi lần
        ghi — xem docstring module, quyết định 1."""
        spec = self.agents[agent_id]; done: list[str] = []; empty: list[str] = []
        for w in writes:
            ns = w["namespace"]
            if ns not in spec.namespaces_write or self.blackboard is None:
                self._audit(spec, "context_rejected", inp,
                            evidence=f"namespace {ns} không thuộc {agent_id} hoặc không có blackboard")
                continue
            kw: dict[str, Any] = {}
            if self.wants_content:
                content = w.get("content")
                content = str(content) if content is not None and str(content).strip() else None
                if content is None: empty.append(ns)
                kw = {"content": content, "project_id": self._context_project(inp)}
            self.blackboard.write(spec.id, ns, str(w["content_ref"]), str(w.get("summary", "")), **kw)
            done.append(ns)
        if done:
            self._audit(spec, "context_written", inp, evidence=",".join(done))
        if empty:
            self._audit(spec, "context_no_content", inp,
                        evidence="chỉ có con trỏ, không có toàn văn: " + ",".join(empty))
        return done

    def publish(self, agent_id: str, inp: E, topic_out: str, payload: dict[str, Any], key: str | None = None,
                tokens: int = 0, model: str = "", context_writes: list[dict[str, Any]] | None = None,
                cache_hit_ratio: float = 0.0, generated: Any = None) -> E:
        """Publish một payload đã sinh dưới danh nghĩa agent (bus validate + kiểm quyền lần nữa), ghi blackboard
        (nếu có context_writes) và ghi audit `produced:<topic>`."""
        spec = self.agents[agent_id]
        try:
            out: E = self.bus.publish(self._new_envelope(inp, topic_out, key or inp.key, spec.id, payload))
        except BusError as e:
            self._audit(spec, "invalid_output", inp, evidence=str(e)[:500], tokens=tokens)
            raise RunnerError(f"{agent_id}: đầu ra không hợp lệ cho {topic_out}: {e}") from e
        if context_writes: self.write_context(agent_id, inp, context_writes)
        self._extra_audit_on_publish(spec, inp, out, topic_out, payload)
        g = generated or self.generated_cls(payloads=[payload], tokens=tokens, model=model,
                                            cache_hit_ratio=cache_hit_ratio)
        self._audit(spec, f"produced:{topic_out}", inp, evidence=self._produced_evidence(g, out.event_id),
                    tokens=tokens, **self._produced_extra(g))
        return out
