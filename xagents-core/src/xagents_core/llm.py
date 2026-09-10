"""Nền chung của lớp LLM hai công ty (K3.3a của ADR gốc 0001).

**Vì sao chỉ có phần nền, không phải cả `llm.py`.** Đặc tả K3.3 viết "gốc là `company/llm.py` (1.128 dòng), tham số
hoá 8 điểm rồi studio thành shim". Đo lại bằng `difflib` trên từng symbol thì giả định đó sai: hai bản đã trôi xa
nhau, chỉ 6/22 symbol cùng tên là trùng gần nguyên văn. Ba lớp nặng nhất lệch hẳn — `ClaudeCodeClient` 0.32,
`OpenAICompatClient` 0.28, `Completion` 0.23 (hai **thuật toán bóc JSON khác nhau**), `LLMConfig` 0.56. Gộp chúng
trong một PR là đổi cách studio đọc mọi đầu ra model, đổi cấu hình, và đổi bốn adapter cùng một lúc — không có
cách nào đọc diff đó mà biết chắc studio còn chạy đúng. Nên K3.3 tách theo *mức rủi ro*: file này là phần **trùng
thật và không đổi hành vi bên nào**; phần đã rẽ nhánh đi các PR sau, mỗi PR một quyết định hợp nhất nói rõ bên nào
được nâng và eval nào chứng minh.

Ba chỗ studio **được nâng** ở đây, đều tương thích ngược (ADR gốc 0001 nguyên tắc 1):

1. `LLMError.status` — studio trước không mang mã HTTP; mặc định `None` nên mọi chỗ `LLMError("…")` cũ vẫn đúng.
   Đây là điều kiện để K3.3d cho studio phân loại lỗi tạm thời theo mã thay vì đoán bằng regex.
2. `TransientError` — studio chưa từng có; chưa ai ném nó trong studio nên chưa đổi gì, nhưng nó phải tồn tại
   trước khi `studio.orchestrator` biết hoãn event thay vì dừng.
3. `reported_model` đọc thêm `canonicalModel` — bản company; studio trước bỏ sót nên model được CLI quy đổi
   (alias → model thật) bị coi là "không khớp".

`ARGV_LIMIT` là tên của company; studio gọi cùng hằng số đó là `CLI_ARGV_MAX` — shim studio giữ cả hai tên.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol, Self, TypeVar, cast

import yaml

from .config import CoreConfig
from .sandbox import SECRET_ENV
from .tools import ToolCall, ToolSpec

__all__ = [
    "ARGV_LIMIT",
    "CACHE_TTL",
    "CLAUDE_EFFORT",
    "CLI_BASE_FLAGS",
    "CLI_SUBTYPE_ERRORS",
    "CODEX_EFFORT",
    "TIERS",
    "TRANSIENT_HTTP",
    "AnthropicClient",
    "ClaudeCodeClient",
    "CodexClient",
    "Completion",
    "FakeClient",
    "LLMConfig",
    "LLMError",
    "ModelClient",
    "OpenAICompatClient",
    "Refused",
    "TransientError",
    "anthropic_input_tokens",
    "cache_control",
    "check_argv",
    "cli_effort_args",
    "cli_env",
    "cli_exit_error",
    "find_codex_binary",
    "load_config",
    "neutral_messages",
    "object_before_trailing_junk",
    "object_in_prose",
    "reported_model",
    "strict_schema",
    "strip_code_fence",
    "system_prompt_args",
]

C = TypeVar("C", bound="LLMConfig")

# Bảng ĐÓNG như `CLAUDE_EFFORT`: giá trị ngoài bảng phải hỏng to thay vì lặng lẽ rơi về mặc định. `None` =
# không khai `ttl` trong body (Anthropic mặc định 5 phút) — mặc định TẮT, body y hệt trước p3.2.
CACHE_TTL = ("5m", "1h")

TIERS = ("strong", "standard", "light")   # light: việc cơ học/ngắn (intake, clarifier, publisher, supervisor) — model rẻ nhất
TRANSIENT_HTTP = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def cache_control(ttl: str | None = None) -> dict[str, str]:
    """Một breakpoint cache của Anthropic. Không cấu hình TTL → đúng `{"type": "ephemeral"}` như trước p3.2.

    TTL 1 giờ KHÔNG cần header `anthropic-beta` (tài liệu Anthropic, mục API reference của prompt
    caching): nó là một khoá trong chính block `cache_control`. Task pack p3.2 giả định phải có beta
    header — không đúng với tài liệu hiện hành, nên ở đây không sinh header nào.
    """
    return {"type": "ephemeral"} if ttl is None else {"type": "ephemeral", "ttl": ttl}


class LLMError(Exception):
    """`status`: mã HTTP của provider khi biết — routing phân loại theo mã (429 quota, 404 thiếu model, 401/403 xác thực)
    thay vì đoán bằng regex trên thông điệp."""
    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message); self.status = status


class Refused(LLMError):
    """Model từ chối trả lời. Không retry mù; để supervisor escalate."""


class TransientError(LLMError):
    """Lỗi vận chuyển (mạng, quá tải, rate limit): thử lại được, không phải lỗi của agent."""


def _check_ttl(value: str) -> str:
    if value not in CACHE_TTL:
        raise LLMError(f"cache_ttl={value!r} không hợp lệ; chỉ nhận {list(CACHE_TTL)} (llm.yaml hoặc <PREFIX>_CACHE_TTL)")
    return value


def neutral_messages(user: str, messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return messages if messages is not None else [{"role": "user", "content": user}]


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured output ở nhiều provider cần `additionalProperties: false` ở mọi object; bản sao, không đổi schema gốc."""
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in {"$schema", "$id", "format"}}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out.setdefault("properties", {})
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    # `walk` trả `Any` vì nó đệ quy trên cây JSON; `strict` của core bắt chỗ này, mypy lỏng của
    # company thì không — schema vào là object nên ra cũng là object.
    return cast("dict[str, Any]", walk(schema))


def reported_model(model_usage: dict[str, Any], requested: str) -> str:
    """`claude -p` liệt kê trong `modelUsage` cả model phụ mà CLI tự gọi (Haiku cho việc lặt vặt) — thường đứng TRƯỚC
    model chính. Chọn khoá khớp tên model đã yêu cầu; không có thì khoá tiêu nhiều output token nhất; rỗng thì tên yêu cầu.

    HỢP NHẤT hai bản, cả hai bên đều được nâng — đây là chỗ duy nhất trong K3.3a mà không bên nào là "gốc":

    - `canonicalModel` là của studio. Company thiếu, nên khi CLI quy đổi alias sang model thật thì company không
      khớp được và rơi xuống nhánh đoán theo output token.
    - Fallback "khoá tiêu nhiều output token nhất" và `if not model_usage: return requested` là của company.
      Studio thiếu, nên studio trả chuỗi RỖNG khi không khớp — mà rỗng đi thẳng vào audit như thể CLI không báo
      model nào.
    """
    if not model_usage: return requested
    for k, v in model_usage.items():
        canonical = str(v.get("canonicalModel", "")) if isinstance(v, dict) else ""
        if k == requested or k.startswith(requested) or requested.startswith(k) or canonical.startswith(requested):
            return k
    def out(k: str) -> int:
        v = model_usage.get(k)
        return int(v.get("outputTokens", 0) or 0) if isinstance(v, dict) else 0
    return max(model_usage, key=out)


# ---------- kết quả một lượt gọi + bóc JSON (K3.3c1) ----------

def strip_code_fence(raw: str) -> str:
    """Bóc code fence bao quanh JSON (model nhỏ hay bọc ```json ... ```).

    Chỉ bỏ fence MỞ ở đầu và fence ĐÓNG ở CUỐI; fence nằm giữa là nội dung thật (research findings hay trích
    đoạn config) — cắt theo nó sẽ chặt cụt JSON giữa chừng. Không có fence thì trả nguyên văn.
    """
    text = raw.strip()
    if not text.startswith("```"):
        return text
    if "\n" in text:
        text = text.split("\n", 1)[1]          # bỏ cả dòng mở (``` hoặc ```json)
    else:
        text = text[3:].lstrip()                # fence một dòng: ```{...}``` hoặc ```json {...}```
        if not text.startswith(("{", "[")):     # bỏ nhãn ngôn ngữ dính liền (```json {...}```)
            text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else text
    text = text.rstrip()
    if text.endswith("```"):                    # fence đóng chỉ khi thật sự ở cuối
        text = text[:-3]
    return text.strip()


def object_before_trailing_junk(text: str) -> dict[str, Any] | None:
    """Một object HOÀN CHỈNH rồi thừa dấu đóng ở cuối → trả object đó; mọi trường hợp khác → None.

    Trượt quan sát được của model khi đầu ra dài (đo 2026-09-05 trên bản ghi eval `researcher`: 14.5k ký tự
    JSON, thừa đúng một `}` ở cuối). Object đứng trước đã đóng đủ và không mơ hồ — bỏ cả lượt vì một dấu thừa
    là phí, y như chuyện chuỗi "null" ở `runner._normalize_nulls`.

    Ranh giới hẹp có chủ đích: chỉ chấp nhận phần dư gồm khoảng trắng và `}`/`]`.
    """
    try:
        obj, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return None
    thua = "".join(text[end:].split())  # bỏ mọi khoảng trắng, kể cả ở giữa các dấu đóng
    if not isinstance(obj, dict) or not thua or set(thua) - {"}", "]"}:
        return None
    return cast("dict[str, Any]", obj)


def object_in_prose(raw: str) -> dict[str, Any] | None:
    """Model kể chuyện rồi mới trả JSON: lấy object trong code fence ĐẦU TIÊN, không được thì quét `{` đầu tới
    `}` cuối và lùi dần dấu đóng. Không có object nào → None.

    Bản của studio. Nó đi TÌM trong văn xuôi, nên chỉ được gọi khi đầu ra **không** bắt đầu bằng JSON — xem
    `Completion.json`.
    """
    m = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", raw, re.DOTALL)   # fence đầu tiên
    if m:
        try: return cast("dict[str, Any]", json.loads(m.group(1)))
        except json.JSONDecodeError: pass
    start = raw.find("{")
    end = raw.rfind("}")
    while start >= 0 and end > start:
        try: return cast("dict[str, Any]", json.loads(raw[start:end + 1]))
        except json.JSONDecodeError: end = raw.rfind("}", start, end)
    return None


@dataclass
class Completion:
    """Kết quả một lượt gọi model, trung lập provider.

    `input_tokens` LUÔN là tổng input đã tính tiền, kể cả phần đọc từ cache và phần ghi cache — mỗi adapter tự
    quy đổi về nghĩa này vì provider đếm khác nhau (Anthropic tách cache ra khỏi `input_tokens`, OpenAI gộp vào
    `prompt_tokens`). `cached_input_tokens` và `cache_write_tokens` chỉ để báo cáo hiệu quả cache, không cộng thêm.

    K3.3c1 hợp nhất hai bản: studio nhận thêm `cache_write_tokens` và `tool_mode` (mặc định 0 / `""` nên mọi
    `Completion(...)` cũ vẫn dựng được), company nhận thêm đường bóc JSON trong văn xuôi.
    """
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str = "end_turn"
    cached_input_tokens: int = 0  # phần input phục vụ từ cache (đã nằm trong input_tokens)
    cache_write_tokens: int = 0   # phần input ghi vào cache lần đầu (đã nằm trong input_tokens)
    tool_calls: list[ToolCall] = field(default_factory=list)  # model muốn gọi tool (rỗng = trả lời cuối)
    # Ai đã chạy vòng tool của lượt này: "" = vòng lặp của runner (mọi provider API); "mcp" = CLI chạy, gọi ngược
    # tool của công ty qua cầu MCP; "cli" = CLI chạy bằng tool RIÊNG của nó. Runner ghi vào audit `tools_used` để
    # người vận hành biết lượt vừa rồi đi hàng rào nào, không phải đoán từ cấu hình.
    tool_mode: str = ""

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_ratio(self) -> float:
        return self.cached_input_tokens / self.input_tokens if self.input_tokens else 0.0

    def json(self) -> dict[str, Any]:
        """JSON object trong câu trả lời. Bốn đường, theo đúng thứ tự này — thứ tự LÀ quyết định hợp nhất:

        1. bóc fence bao ngoài rồi `json.loads` (đường thường);
        2. object hoàn chỉnh + thừa dấu đóng ở cuối (bản company, `object_before_trailing_junk`);
        3. **chỉ khi đầu ra không bắt đầu bằng JSON**: tìm trong văn xuôi (bản studio, `object_in_prose`);
        4. hết đường → `LLMError` kèm trích đoạn quanh vị trí lỗi.

        Điều kiện ở bước 3 là chỗ hai bản mâu thuẫn thật, và là quyết định của PR này. Company cố ý ĐỎ khi JSON
        hỏng **giữa cấu trúc** (model đóng object sớm rồi viết tiếp `,{...}`): cứu chỗ đó là đoán ý model, mà
        đoán sai thì ticket đi tiếp với một nửa dữ liệu. Nhưng phép quét của studio lại cứu đúng một tình huống
        company chưa cứu được: model **kể chuyện trước rồi mới trả JSON** — thường gặp khi model vừa chạy tool.
        Phân biệt hai tình huống bằng chỗ JSON bắt đầu: có văn xuôi đứng trước thì đi tìm, còn đầu ra tự nhận là
        JSON ngay từ ký tự đầu mà hỏng thì vẫn đỏ như cũ.
        """
        text = strip_code_fence(self.text)
        try:
            return cast("dict[str, Any]", json.loads(text))
        except json.JSONDecodeError as e:
            if (obj := object_before_trailing_junk(text)) is not None:
                return obj
            if not text.lstrip().startswith(("{", "[")) and (obj := object_in_prose(self.text)) is not None:
                return obj
            # chỉ trích đoạn quanh vị trí lỗi, không đổ cả đầu ra vào log
            near = text[max(0, e.pos - 120):e.pos + 120]
            raise LLMError(f"đầu ra không phải JSON: {e} — gần vị trí lỗi: ...{near}...") from e

# ---------- cấu hình (K3.3b) ----------

@dataclass
class LLMConfig:
    """Cấu hình lớp LLM của MỘT công ty — phần hai công ty khai giống nhau.

    Đây là bước b của K3.3: `LLMConfig`/`load_config` là chỗ duy nhất trong `llm.py` mà hai bản chỉ khác nhau ở
    *tên biến môi trường* và *tập trường phụ*, chứ không khác thuật toán (khác thuật toán là bốn adapter và
    `Completion.json()` — bước c). `difflib` đo `LLMConfig` 0.56 chủ yếu vì company có 14 trường studio không có
    (CLI/MCP, giá, ngân sách, sandbox, retry): bỏ khối trường đó ra thì phần còn lại trùng gần nguyên văn, kể cả
    thứ tự dòng trong `backend_config`.

    **Vì sao kế thừa chứ không tham số hoá bằng cờ.** Trường phụ của company là *dữ liệu*, không phải hành vi:
    `retries`, `prices`, `sandbox`… phải có kiểu thật để mypy bắt được chỗ đọc sai tên. Nhét chúng vào một
    `dict[str, Any]` chung ở core là đổi 14 trường có kiểu lấy một túi `Any` — mất đúng thứ mà `strict` của core
    sinh ra để giữ. Nên core giữ khung + khoá dùng chung, mỗi công ty kế thừa và **ghi đè ba móc**
    (`apply_backend_yaml`, `apply_yaml`, `apply_env`), mỗi móc gọi `super()` trước rồi đọc thêm phần của mình.

    `PREFIX` là `ClassVar` (không phải trường) vì hai lẽ: `backend_config` sao chép `self.__dict__` nên mọi trường
    đều bị nhân bản xuống từng backend — tiền tố công ty thì không có lý do gì để khác nhau giữa hai backend của
    cùng một công ty; và một `LLMConfig()` dựng tay trong test vẫn phải báo lỗi đúng tên biến, không phải chuỗi
    rỗng vì người dựng quên truyền tiền tố.
    """
    PREFIX: ClassVar[str] = ""   # company/studio gán = CORE.prefix; core không tự biết mình phục vụ ai

    provider: str = "fake"       # công ty ghi đè mặc định của mình (company: anthropic)
    models: dict[str, str] = field(default_factory=lambda: dict.fromkeys(TIERS, ""))
    base_url: str | None = None
    api_key: str | None = None
    max_tokens: int = 16_000
    effort: dict[str, str] = field(default_factory=lambda: {"strong": "high", "standard": "medium", "light": "low"})
    extra: dict[str, Any] = field(default_factory=dict)  # tham số provider-specific, truyền thẳng vào request
    config_dir: str | None = None    # claude-code: CLAUDE_CONFIG_DIR / codex: CODEX_HOME riêng → tài khoản khác trên cùng máy
    binary: str | None = None        # đường dẫn CLI (claude / codex) khi không có trên PATH
    name: str = "default"            # tên backend, hiện trong ghi chú audit khi xoay
    backends: list[dict[str, Any]] = field(default_factory=list)   # mỗi phần tử = một backend, cùng khoá như cấp trên
    routing: dict[str, Any] = field(default_factory=dict)          # cooldown_s, transient_cooldown_s, prefer{tier: backend}
    max_input_chars: int = 120_000   # trần ký tự prompt (≈ 37k token); runner cắt payload/blackboard theo `context.py`
    # TTL của breakpoint cache Anthropic. `None` = không khai `ttl` (mặc định 5 phút của provider) → body
    # y hệt trước p3.2. `"1h"` giữ entry qua khoảng nghỉ dài NHƯNG giá GHI cache gấp đôi (1.25× → 2×), nên
    # chỉ lãi khi cùng một tiền tố được đọc lại ≥ 3 lần trong giờ đó — mặc định TẮT có chủ đích.
    #
    # Khác `max_input_chars` (thuộc cả hệ): TTL là tính năng của PROVIDER, nên đọc được ở cấp backend —
    # một tài khoản Anthropic bật TTL dài không buộc backend OpenAI-compat bên cạnh phải hiểu khoá này.
    cache_ttl: str | None = None

    def model_for(self, tier: str) -> str:
        """light → standard → strong: backend không có model rẻ thì dùng model tầm trung, không bao giờ lùi lên tier cao
        hơn yêu cầu trừ khi đó là model duy nhất."""
        m = self.models.get(tier) or self.models.get("standard") or self.models.get("strong") or ""
        if not m:
            raise LLMError(f"chưa cấu hình model cho tier `{tier}` ({self.PREFIX}_MODEL_{tier.upper()} hoặc llm.yaml)")
        return m

    def tiers_configured(self) -> frozenset[str]:
        return frozenset(t for t in TIERS if self.models.get(t))

    def apply_backend_yaml(self, data: Mapping[str, Any]) -> None:
        """Khoá đọc được ở CẢ HAI cấp: gốc `llm.yaml` và từng phần tử `backends:`. Công ty ghi đè để đọc thêm khoá
        của mình (company: `cli_*`, `mcp_*`) — thêm ở đây thì cả hai cấp cùng hiểu, không phải nhớ sửa hai chỗ."""
        self.provider = data.get("provider", self.provider)
        self.models.update({k: str(v) for k, v in (data.get("models") or {}).items()})
        self.effort.update(data.get("effort") or {})
        self.base_url = data.get("base_url", self.base_url)
        self.max_tokens = int(data.get("max_tokens", self.max_tokens))
        if "extra" in data: self.extra = dict(data.get("extra") or {})
        if data.get("cache_ttl") is not None: self.cache_ttl = _check_ttl(str(data["cache_ttl"]))

    def apply_yaml(self, data: Mapping[str, Any]) -> None:
        """Khoá chỉ đọc được ở GỐC `llm.yaml`."""
        self.apply_backend_yaml(data)
        # Cố ý KHÔNG ở trong `apply_backend_yaml`: trần prompt là thuộc tính của cả hệ, không của một backend —
        # mỗi backend một trần khác nhau thì cùng một agent bị cắt khác nhau tuỳ tài khoản nào còn hạn mức.
        self.max_input_chars = int(data.get("max_input_chars", self.max_input_chars))
        self.backends = [dict(b) for b in (data.get("backends") or []) if isinstance(b, dict)]
        self.routing = dict(data.get("routing") or {})

    def apply_env(self, env: Mapping[str, str], core: CoreConfig) -> None:
        """Biến môi trường thắng file. Tên biến đi qua `core.env_name` — core không tự ghép tiền tố ở nơi dùng."""
        if env.get(core.env_name("LLM_PROVIDER")):   # một provider được chỉ đích danh → bỏ `backends:`
            self.provider, self.backends = env[core.env_name("LLM_PROVIDER")], []
        for t in TIERS:
            if env.get(core.env_name(f"MODEL_{t.upper()}")): self.models[t] = env[core.env_name(f"MODEL_{t.upper()}")]
        self.base_url = env.get(core.env_name("LLM_BASE_URL"), self.base_url)
        self.api_key = env.get(core.env_name("LLM_API_KEY"), self.api_key)
        if env.get(core.env_name("MAX_INPUT_CHARS")):
            self.max_input_chars = int(env[core.env_name("MAX_INPUT_CHARS")])
        if env.get(core.env_name("CACHE_TTL")):
            self.cache_ttl = _check_ttl(env[core.env_name("CACHE_TTL")])

    def select_backends(self, env: Mapping[str, str], core: CoreConfig) -> None:
        """`<PREFIX>_LLM_BACKENDS` lọc/sắp thứ tự `backends:`. Tách khỏi `apply_env` và gọi SAU CÙNG trong
        `load_config`: nó đọc `self.backends` mà `apply_env` của công ty có thể còn ghi vào, và một lớp con quên
        gọi `super()` đúng chỗ thì bộ lọc lặng lẽ chạy trên danh sách cũ."""
        raw = env.get(core.env_name("LLM_BACKENDS"))
        if not raw: return
        wanted = [s.strip() for s in raw.split(",") if s.strip()]
        by_name = {str(b.get("name") or b.get("provider")): b for b in self.backends}
        missing = [w for w in wanted if w not in by_name]
        if missing:
            raise LLMError(f"{core.env_name('LLM_BACKENDS')} nhắc backend không có trong llm.yaml: {missing}")
        self.backends = [by_name[w] for w in wanted]
        if self.routing.get("prefer"):   # prefer trỏ backend đã bị lọc bỏ thì bỏ mục đó, không phải lỗi cấu hình
            self.routing["prefer"] = {t: n for t, n in self.routing["prefer"].items() if n in wanted}

    def backend_config(self, data: Mapping[str, Any]) -> Self:
        """Cấu hình cho một phần tử `backends:`: thừa kế mọi khoá dùng chung (retry, giá, trần ký tự) từ cấp trên,
        ghi đè provider / models / base_url / api_key / effort / extra / max_tokens theo phần tử.

        `type(self)` chứ không phải `LLMConfig`: lớp con của công ty phải sinh ra chính lớp con đó, nếu không mọi
        trường phụ (giá, ngân sách, sandbox) biến mất ngay khi cấu hình có `backends:`.
        """
        cfg = type(self)(**{k: v for k, v in self.__dict__.items() if k not in {"backends", "routing"}})
        cfg.models = dict(self.models) if data.get("inherit_models") else dict.fromkeys(TIERS, "")
        cfg.effort, cfg.extra = dict(self.effort), dict(self.extra)
        cfg.apply_backend_yaml(data)
        cfg.name = str(data.get("name") or cfg.provider)
        cfg.config_dir = str(data["config_dir"]) if data.get("config_dir") else None
        cfg.binary = str(data["binary"]) if data.get("binary") else None
        if data.get("api_key"): cfg.api_key = str(data["api_key"])
        if data.get("api_key_env"): cfg.api_key = os.environ.get(str(data["api_key_env"]), cfg.api_key)
        return cfg


def load_config(core: CoreConfig, path: Path | None = None, *, cls: type[C]) -> C:
    """`llm.yaml` của công ty rồi biến môi trường đè lên. `cls` là lớp cấu hình của công ty — bắt buộc, không có
    mặc định: một `load_config` lỡ trả `LLMConfig` trần cho company thì mất im lặng cả `prices` lẫn `sandbox`,
    và mypy không cứu được vì lớp con vẫn là `LLMConfig`."""
    cfg = cls()
    p = path or core.config_file
    if p.exists():
        cfg.apply_yaml(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    cfg.apply_env(os.environ, core)
    cfg.select_backends(os.environ, core)
    return cfg

# ---------- adapter CLI (claude / codex): phần không phụ thuộc công ty ----------

ARGV_LIMIT = 30_000 if os.name == "nt" else 120_000   # Windows: CreateProcess ~32K ký tự; Linux: một đối số ≤ 128K

# `--effort` theo tier — bảng ĐÓNG, giá trị ngoài bảng phải hỏng to thay vì rơi về mặc định (bài học `none` của
# codex: cấu hình nói một đằng, CLI chạy một nẻo).
CLAUDE_EFFORT = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORT = {"none": "none", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh",
                "max": "xhigh", "minimal": "minimal"}

# `--no-session-persistence`: mỗi lượt `-p` mặc định ghi transcript (chứa mã của khách, kịch bản, dossier, kết quả
# web) ra ~/.claude/projects; gọi hàng trăm lượt là hàng trăm bản sao nằm ngoài kho của công ty.
CLI_BASE_FLAGS = ("--no-session-persistence",)

# `subtype` trong JSON của `claude -p` nói vì sao phiên dừng; `result` có thể vắng ở các subtype lỗi. Adapter chỉ
# nhìn `result` thì hết lượt bị báo thành "thiếu trường result" — người vận hành không biết phải tăng gì.
CLI_SUBTYPE_ERRORS = {
    "error_max_turns": "CLI hết lượt (tăng trần lượt tool hoặc chia nhỏ việc)",
    "error_max_budget_usd": "CLI chạm trần chi phí `--max-budget-usd`",
    "error_max_structured_output_retries": "CLI không ép được đầu ra đúng JSON Schema sau nhiều lần thử",
    "error_during_execution": "CLI lỗi khi đang chạy",
}


@contextmanager
def system_prompt_args(system: str) -> Iterator[list[str]]:
    """`--system-prompt-file <path>` thay vì `--system-prompt <text>`: agent nhiều skill (vd. researcher) có system
    prompt riêng đã sát hoặc vượt ARGV_LIMIT trên Windows; ghi ra file tạm thì argv chỉ còn một đường dẫn ngắn,
    không đụng trần argv nữa (nội dung vẫn không qua stdin, để không lẫn với prompt/schema của user message)."""
    fd, path = tempfile.mkstemp(prefix="claude-sp-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f: f.write(system)
        yield ["--system-prompt-file", path]
    finally:
        Path(path).unlink(missing_ok=True)


def cli_effort_args(effort: dict[str, str], tier: str) -> list[str]:
    """`--effort <mức>` cho tier; không khai tier → không thêm cờ (CLI dùng mặc định của nó); khai sai → lỗi rõ."""
    level = effort.get(tier)
    if level is None: return []
    if level not in CLAUDE_EFFORT:
        raise LLMError(f"claude-code: effort `{level}` cho tier `{tier}` không hợp lệ; CLI chỉ nhận "
                       f"{'|'.join(CLAUDE_EFFORT)} (khai `effort:` riêng cho backend này trong llm.yaml)")
    return ["--effort", level]


def find_codex_binary(binary: str = "codex") -> str:
    """`codex` trên PATH; không có thì tìm bản đi kèm app Codex trên Windows (%LOCALAPPDATA%/OpenAI/Codex/bin/*/codex.exe)."""
    found = shutil.which(binary)
    if found: return found
    base = os.environ.get("LOCALAPPDATA")
    if base:
        cands = sorted(Path(base).glob("OpenAI/Codex/bin/*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands: return str(cands[0])
    return binary


# ---------- K3.3c2: ba adapter trùng gần nguyên văn ----------
#
# `AnthropicClient` 0.81 · `CodexClient` 0.82 · `FakeClient` 0.88 (difflib trên từng symbol, đo sau K3.3c1).
# Mọi điểm lệch đều là company đúng hơn — không phải "company là gốc", mà vì từng điểm một studio đang MẤT thông
# tin hoặc mất một lớp bảo vệ:
#
# 1. `timeout=600` cho SDK Anthropic. Studio không đặt, nên một request treo giữ luôn cả orchestrator: vòng lặp
#    tuần tự, một tiến trình, không ai gỡ được ngoài Ctrl-C.
# 2. `TransientError` thay vì `LLMError` cho lỗi mạng / 429 / timeout. Studio ném `LLMError` cho tất cả, mà
#    `LLMError` là lỗi NỘI DUNG — orchestrator dừng thay vì hoãn event cho nhịp sau. Một nhịp mạng chập làm hỏng
#    cả lượt. (Studio chưa bắt `TransientError` ở orchestrator — đó là K3.3d; ném đúng loại là điều kiện trước.)
# 3. `cache_write_tokens`. Anthropic để token GHI vào cache ra ngoài `input_tokens`; studio bỏ trường này nên
#    `audit-log.tokens` thiếu đúng phần đắt nhất của lượt đầu tiên, và trần ngân sách không bao giờ chạm.
# 4. Prompt của codex đi qua **stdin** thay vì argv, kèm `check_argv`. Prompt dài trên argv làm hệ điều hành thoát
#    với `Argument list too long` — thông điệp không nói gì về prompt (đúng khuôn 1 của TRAPS §1).
# 5. `workdir` trong chữ ký `complete`. Studio không dùng, nhận `None`; có mặt để một `ModelClient` duy nhất phục
#    vụ được cả hai công ty.
#
# `_run`/`runner` của `CodexClient` đổi chữ ký `(args) -> str` thành `(args, stdin) -> str` — đây là **đổi thật**,
# không tương thích ngược, và test studio nào chèn runner phải sửa theo. Đó là cái giá của điểm 4.


class ModelClient(Protocol):
    """Giao diện DUY NHẤT runner của hai công ty biết. Adapter provider nằm sau nó; đổi model/provider là đổi
    cấu hình, không đổi mã gọi (ADR gốc 0001 nguyên tắc "trung lập provider")."""

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion: ...  # pragma: no cover
    # `pragma: no cover` như các stub Protocol ở `evals.py`: thân `...` là KHAI BÁO KIỂU, không có đường chạy
    # nào tới nó (mọi lời gọi đi vào hiện thực cụ thể). Đây là miễn trừ cho thứ không thực thi được, không
    # phải cho thứ chưa được test — đừng dùng khuôn này cho code có nhánh thật.


def cli_env(keep_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    """Env cho tiến trình CLI model (claude/codex): bỏ mọi biến trông như khoá, trừ tiền tố mà CLI cần để đăng
    nhập. Khoá `COMPANY_LLM_*`/`STUDIO_LLM_*` của công ty không bao giờ đi theo.

    Trước khi có hàm này, adapter truyền nguyên `os.environ` — nghĩa là khoá TTS/ảnh/YouTube của phòng ban đi
    thẳng vào tiến trình con, thứ không lượt gọi model nào cần tới."""
    out = {}
    for k, v in os.environ.items():
        if k.upper().startswith(("COMPANY_LLM", "STUDIO_LLM")): continue
        if SECRET_ENV.search(k) and not k.upper().startswith(tuple(p.upper() for p in keep_prefixes)): continue
        out[k] = v
    return out


def check_argv(args: list[str]) -> None:
    """Prompt dài không được đi qua argv (đã chuyển sang stdin); phần còn lại vượt trần thì báo rõ thay vì để hệ
    điều hành thoát với `Argument list too long` / `The command line is too long` khó hiểu (system prompt tự nó đi
    qua `--system-prompt-file`, xem `system_prompt_args`, nên không còn là nguồn chính gây vượt trần)."""
    total = sum(len(a) + 1 for a in args)
    if total > ARGV_LIMIT:
        raise LLMError(f"argv của CLI dài {total} ký tự > {ARGV_LIMIT} (system prompt quá lớn; rút gọn prompt/skill hoặc "
                       "đổi provider API)")


def anthropic_input_tokens(usage: Any) -> tuple[int, int, int]:
    """(tổng input tính tiền, đọc từ cache, ghi vào cache) từ `usage` của Anthropic.

    Anthropic để token cache RA NGOÀI `input_tokens`. Không cộng lại thì `audit-log.tokens` bỏ sót gần hết system
    prompt (phần lặp giữa các lượt nằm hết trong cache) và hạn mức của supervisor sẽ không bao giờ chạm."""
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return usage.input_tokens + read + write, read, write


class AnthropicClient:
    """Claude qua SDK chính thức: streaming, adaptive thinking, structured output theo JSON Schema."""

    def __init__(self, cfg: LLMConfig, timeout: float = 600.0):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("cài SDK: uv sync --extra anthropic") from e
        self.cfg = cfg
        self._anthropic = anthropic
        # Không có timeout thì một request treo giữ luôn cả orchestrator (vòng lặp tuần tự, một tiến trình).
        self._client = anthropic.Anthropic(timeout=timeout)

    @staticmethod
    def _messages(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Định dạng trung lập → content block của Anthropic (tool_use / tool_result)."""
        out: list[dict[str, Any]] = []
        for m in msgs:
            if m["role"] == "assistant":
                blocks: list[dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                blocks += [{"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["args"]} for t in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)  # nhiều tool_result cùng một lượt user
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model_for(model_tier), max_tokens=self.cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": cache_control(self.cfg.cache_ttl)}],
            messages=self._messages(neutral_messages(user, messages)),
            thinking={"type": "adaptive"},
            output_config={"effort": self.cfg.effort.get(model_tier, "medium"),
                           "format": {"type": "json_schema", "schema": strict_schema(schema)}},
            **self.cfg.extra,
        )
        if tools:
            specs = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
            # Breakpoint cache THỨ HAI, trên định nghĩa tool CUỐI (p3.2b). Thứ tự dựng tiền tố của
            # Anthropic là `tools` → `system` → `messages`, nên một breakpoint duy nhất trên system vẫn
            # cache cả phần tool — nhưng cache theo TIỀN TỐ: đổi một byte trong system là mất luôn phần
            # tool đứng trước nó. Mà system prompt ĐỔI theo pha (`spec.system_prompt(phase)`, ADR-0037)
            # trong khi danh sách tool thì không, nên mỗi lần đổi pha là ghi lại cache cho cả bảng tool.
            # Breakpoint trên tool cuối cho phần tool một điểm đọc riêng, sống qua mọi thay đổi của system.
            #
            # KHÔNG đặt breakpoint trong `messages`: phần đó xoay vòng mỗi lượt (`_prune` tỉa tool result
            # cũ — ADR-0007), tức là SỬA lịch sử, nên một marker ở đó chỉ ghi entry mới rồi không ai đọc.
            specs[-1]["cache_control"] = cache_control(self.cfg.cache_ttl)
            kwargs["tools"] = specs
        try:
            with self._client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except self._anthropic.APIConnectionError as e:
            raise TransientError(f"lỗi mạng: {e}") from e
        except self._anthropic.APIStatusError as e:
            if e.status_code in TRANSIENT_HTTP:
                raise TransientError(f"API {e.status_code}: {e.message}", status=e.status_code) from e
            raise LLMError(f"API {e.status_code}: {e.message}", status=e.status_code) from e
        if msg.stop_reason == "refusal":
            raise Refused(f"model từ chối: {getattr(getattr(msg, 'stop_details', None), 'category', None)}")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        calls = [ToolCall(id=b.id, name=b.name, args=dict(b.input or {})) for b in msg.content if b.type == "tool_use"]
        inp, read, write = anthropic_input_tokens(msg.usage)
        return Completion(text=text, input_tokens=inp, output_tokens=msg.usage.output_tokens,
                          model=msg.model, stop_reason=msg.stop_reason or "end_turn",
                          cached_input_tokens=read, cache_write_tokens=write, tool_calls=calls)


class CodexClient:
    """Gọi `codex exec --json` như một model backend: mỗi lượt một tiến trình con, sandbox read-only trong
    thư mục rỗng (không tool của công ty; Codex có thể tự đọc thư mục rỗng đó, vô hại), system prompt ghép vào đầu prompt vì
    CLI không có cờ system riêng. Schema nhúng vào prompt, không dùng `--output-schema` (strict mode của OpenAI bắt mọi thuộc
    tính phải `required`, không hợp schema topic có trường tuỳ chọn). Đầu ra JSONL: `item.completed` (agent_message) là câu trả lời, `turn.completed` mang
    `usage` (input đã gồm phần cache như OpenAI), `error` / `turn.failed` là lỗi (CLI vẫn thoát mã 0).
    Nhiều tài khoản ChatGPT trên một máy: `config_dir` → CODEX_HOME riêng (`CODEX_HOME=~/.codex-acc2 codex login`)."""

    def __init__(self, cfg: LLMConfig, binary: str | None = None, timeout: float = 900.0,
                 runner: Callable[[list[str], str], str] | None = None):
        self.cfg = cfg
        explicit = binary or self.cfg.binary
        self.binary = (shutil.which(explicit) or explicit) if explicit else find_codex_binary()
        self.timeout = timeout
        self.workdir = Path(tempfile.mkdtemp(prefix="codex-empty-"))
        self.env = cli_env(keep_prefixes=("OPENAI_", "CODEX_"))  # như ClaudeCodeClient: không mang khoá công ty vào CLI
        if self.cfg.config_dir:
            self.env["CODEX_HOME"] = str(Path(self.cfg.config_dir).expanduser())
        self._run = runner or self._subprocess

    def _subprocess(self, args: list[str], stdin: str) -> str:
        import subprocess
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               input=stdin, timeout=self.timeout, env=self.env)
        except FileNotFoundError as e:
            raise LLMError(f"không tìm thấy `{self.binary}` (cài Codex CLI hoặc đặt `binary:` cho backend)") from e
        except subprocess.TimeoutExpired as e:
            raise TransientError(f"codex exec quá {self.timeout}s") from e
        if r.returncode != 0:
            detail = (r.stdout[-600:] + "\n" + r.stderr[-300:]).strip()
            raise LLMError(f"codex exec thoát mã {r.returncode}: {detail}")
        return r.stdout

    def _args(self, model: str, effort: str) -> list[str]:
        # Không có prompt vị trí: `codex exec` đọc prompt từ stdin (system + user + schema đều nằm trong đó).
        return [self.binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                "-C", str(self.workdir), "--json", "-m", model,
                "-c", f"model_reasoning_effort={CODEX_EFFORT.get(effort, 'medium')}"]

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        if tools:
            raise LLMError("codex không hỗ trợ tool-use của công ty; agent cần tool phải đi backend anthropic/openai")
        model = self.cfg.model_for(model_tier)
        msgs = neutral_messages(user, messages)
        body = msgs[0]["content"] if len(msgs) == 1 else "\n\n".join(f"[{m['role']}]\n{m.get('content') or ''}" for m in msgs)
        hint = "# JSON Schema bắt buộc cho câu trả lời\n```json\n" + json.dumps(schema, ensure_ascii=False) + "\n```"
        prompt = (f"# Vai trò và quy tắc\n{system}\n\n# Yêu cầu\n{body}\n\n{hint}\n\n"
                  "Trả lời DUY NHẤT một JSON đúng schema trên, không giải thích, không đọc hay chạy gì trong thư mục làm việc.")
        args = self._args(model, self.cfg.effort.get(model_tier, "medium"))
        check_argv(args)
        out = self._run(args, prompt)
        texts: list[str] = []; usage: dict[str, Any] = {}; errors: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"): continue
            try: ev = json.loads(line)
            except json.JSONDecodeError: continue
            t = ev.get("type")
            if t == "item.completed":
                item = ev.get("item") or {}
                if item.get("type") == "agent_message": texts.append(str(item.get("text") or ""))
                elif item.get("type") == "error": errors.append(str(item.get("message") or ""))
            elif t == "turn.completed": usage = ev.get("usage") or {}
            elif t == "error": errors.append(str(ev.get("message") or ""))
            elif t == "turn.failed": errors.append(str((ev.get("error") or {}).get("message") or ""))
        fatal = [e for e in errors if "Defaulting to fallback metadata" not in e]   # cảnh báo metadata model không phải lỗi
        if fatal and not texts:
            msg = " | ".join(fatal)[:400]
            low = msg.lower()
            if any(s in low for s in ("429", "rate", "limit", "quota", "overloaded", "usage", "503", "502", "timeout")):
                raise TransientError(f"codex exec: {msg}")
            if "not logged in" in low or "login" in low:
                raise LLMError(f"codex exec: chưa đăng nhập (CODEX_HOME={self.env.get('CODEX_HOME', '~/.codex')}): {msg}")
            raise LLMError(f"codex exec lỗi: {msg}")
        if not texts:
            raise LLMError(f"codex exec không trả agent_message: {out[:300]}")
        inp = int(usage.get("input_tokens", 0) or 0); cached = int(usage.get("cached_input_tokens", 0) or 0)
        write = int(usage.get("cache_write_input_tokens", 0) or 0)
        return Completion(text=texts[-1], input_tokens=inp, output_tokens=int(usage.get("output_tokens", 0) or 0),
                          model=model, cached_input_tokens=cached, cache_write_tokens=write)


# ---------- provider: giả (test / eval offline) ----------

@dataclass
class FakeClient:
    """`responses` là hàng đợi dict trả về theo thứ tự, hoặc `handler(system, user)` sinh payload."""
    responses: list[dict[str, Any]] = field(default_factory=list)
    handler: Callable[[str, str], dict[str, Any]] | None = None
    tokens_per_call: tuple[int, int] = (1_000, 300)
    calls: list[dict[str, Any]] = field(default_factory=list)
    # tool_handler(messages, tools) → danh sách ToolCall; rỗng = model trả lời cuối (qua handler/responses như thường)
    tool_handler: Callable[[list[dict[str, Any]], list[ToolSpec]], list[ToolCall]] | None = None

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        msgs = neutral_messages(user, messages)
        # Hai thứ KHÁC NHAU khi có `messages`, và cả hai đều cần:
        #   `user`     = lượt user thật sự gửi đi (lấy từ `messages`) — bản company, thứ model đọc.
        #   `user_arg` = đối số caller truyền vào — bản studio, và là thứ `evals.prompt_key(system, user)` băm,
        #                nên test nào muốn đối chiếu khoá eval phải dùng nó, không phải `user`.
        # K3.3c2 giữ cả hai thay vì chọn một: bản cũ của mỗi bên đều mất đúng thông tin mà bên kia dùng.
        self.calls.append({"system": system, "user": next(m["content"] for m in msgs if m["role"] == "user"),
                           "user_arg": user, "schema": schema, "model_tier": model_tier,
                           "cache_key": cache_key, "tools": [t.name for t in tools or []], "messages": msgs})
        if tools and self.tool_handler:
            wanted = self.tool_handler(msgs, tools)
            if wanted:
                return Completion(text="", input_tokens=self.tokens_per_call[0], output_tokens=self.tokens_per_call[1],
                                  model=f"fake-{model_tier}", stop_reason="tool_use", tool_calls=list(wanted))
        if self.handler:
            payload = self.handler(system, user)
        elif self.responses:
            payload = self.responses.pop(0)
        else:
            raise LLMError("FakeClient hết câu trả lời")
        return Completion(text=json.dumps(payload, ensure_ascii=False), input_tokens=self.tokens_per_call[0],
                          output_tokens=self.tokens_per_call[1], model=f"fake-{model_tier}")


# ---------- K3.3c3 bước 1: OpenAICompatClient ----------
#
# difflib 0.73 sau c2 — company là **tập cha** của studio: cùng hình dạng, cùng thứ tự, hơn đúng một method và
# 46 dòng. Khác hẳn `ClaudeCodeClient` (0.39) vốn hơn 83 dòng và ba method của cầu MCP — cái đó là quyết định
# riêng, ở PR sau.
#
# Năm điểm studio ĐANG THIẾU, mỗi cái là một lớp bảo vệ chứ không phải tính năng:
#
# 1. `_rejects` — **bug thật của studio**, không chỉ là thiếu sót. Bản studio tắt `json_schema` /
#    `prompt_cache_key` khi gặp BẤT KỲ HTTP 400 nào. Một 400 vì prompt quá dài, hay vì một tham số khác sai, do
#    đó tắt vĩnh viễn structured output cho cả tiến trình — và im lặng, vì lượt sau vẫn "chạy được", chỉ là
#    chạy ở chế độ kém hơn. Bản company đòi thân lỗi phải NHẮC TỚI đúng tính năng đang dò.
# 2. Lỗi mạng và mã HTTP tạm thời → `TransientError` (hoãn) thay vì `LLMError` (dừng) — cùng chủ đề với c2.
#    Kèm bắt `TimeoutError`, thứ `URLError` không phủ.
# 3. `finish_reason == "length"` được nhận diện RIÊNG. Trước đó lượt này lọt xuống dưới với `text` cụt, rồi
#    runner báo "đầu ra không phải JSON" — người đọc đi sửa prompt trong khi việc cần làm là tăng `max_tokens`.
#    Model "thinking" đặc biệt dễ dính: token suy nghĩ tính vào cùng hạn mức.
# 4. Thân RỖNG với HTTP 200 được nhận diện RIÊNG. Đây là khuôn 1 của TRAPS §1 đúng nguyên văn: cả hai bên đều
#    tưởng bình thường. Nguyên nhân thật (đo 2026-09-04): server không hiện thực `response_format: json_schema`
#    theo chuẩn mà trả JSON qua `tool_calls`, nên `message.content` rỗng trong khi dữ liệu nằm nguyên ở
#    `tool_calls[0].function.arguments`. Vì là 200, `_json_schema_ok` vẫn True và MỌI lượt sau hỏng y hệt.
# 5. `cached_tokens` chỉ để báo cáo, không cộng thêm — `prompt_tokens` của OpenAI ĐÃ gồm phần cache (ngược với
#    Anthropic, xem `anthropic_input_tokens`). Cộng lần nữa là thổi phồng số token trong audit.

class OpenAICompatClient:
    """POST {base_url}/chat/completions. Dùng `response_format: json_schema` nếu server hỗ trợ; nếu server từ chối
    (400) thì lùi về `json_object` + schema nhúng trong prompt. Chạy với OpenAI, Ollama, Groq, vLLM, LM Studio..."""

    def __init__(self, cfg: LLMConfig, timeout: float = 600.0):
        self.cfg = cfg
        self.base_url = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = self.cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self._json_schema_ok: bool | None = None
        self._cache_key_ok: bool | None = None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                # `cast` như ba chỗ `json.loads` khác trong file: mypy `strict` của core không nhận `Any`
                # ngầm, mà mypy lỏng của company thì bỏ qua — đúng loại chỗ core bắt được còn bốn package
                # kia thì không.
                return cast("dict[str, Any]", json.loads(r.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            msg = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}"
            raise (TransientError if e.code in TRANSIENT_HTTP else LLMError)(msg, status=e.code) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(f"lỗi mạng: {getattr(e, 'reason', e)}") from e

    @staticmethod
    def _rejects(e: LLMError, *features: str) -> bool:
        """HTTP 400 mà thân lỗi nhắc tới tính năng đang dò (`response_format`, `prompt_cache_key`...). 400 vì lý do
        khác (prompt quá dài, tham số khác sai) không được quy cho tính năng này rồi tắt nó vĩnh viễn."""
        msg = str(e)
        return msg.startswith("HTTP 400") and any(f in msg for f in features)

    def _post_cacheable(self, body: dict[str, Any]) -> dict[str, Any]:
        """Như `_post`, nhưng nếu server từ chối vì không biết `prompt_cache_key` thì gỡ ra và thôi gửi từ lần sau.
        Tách riêng khỏi dò `json_schema` để một lỗi 400 không bị quy sai cho tính năng kia."""
        try:
            data = self._post(body)
        except LLMError as e:
            if "prompt_cache_key" not in body or not self._rejects(e, "prompt_cache_key"):
                raise
            self._cache_key_ok = False
            data = self._post({k: v for k, v in body.items() if k != "prompt_cache_key"})
        else:
            if "prompt_cache_key" in body:
                self._cache_key_ok = True
        return data

    @staticmethod
    def _messages(system: str, msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in msgs:
            if m["role"] == "assistant":
                a: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    a["tool_calls"] = [{"id": t["id"], "type": "function", "function": {
                        "name": t["name"], "arguments": json.dumps(t["args"], ensure_ascii=False)}} for t in m["tool_calls"]]
                out.append(a)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        model = self.cfg.model_for(model_tier)
        msgs = self._messages(system, neutral_messages(user, messages))
        base: dict[str, Any] = {"model": model, "max_tokens": self.cfg.max_tokens, **self.cfg.extra, "messages": msgs}
        if tools:
            base["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.parameters}} for t in tools]
        # Prompt cache: system prompt của mỗi agent là bất biến (ADR-0004) nên định tuyến theo agent id cho tỉ lệ
        # hit cao nhất. Server không hiểu tham số này thì bỏ qua; nếu từ chối (400) thì gửi lại không có nó.
        if cache_key and self._cache_key_ok is not False:
            base["prompt_cache_key"] = cache_key
        data: dict[str, Any] | None = None
        if self._json_schema_ok is not False:
            try:
                data = self._post_cacheable({**base, "response_format": {"type": "json_schema", "json_schema": {
                    "name": "payload", "strict": True, "schema": strict_schema(schema)}}})
                self._json_schema_ok = True
            except LLMError as e:
                if not self._rejects(e, "response_format", "json_schema"): raise
                self._json_schema_ok = False
        if data is None:
            hint = "\n\n# JSON Schema bắt buộc\n```json\n" + json.dumps(schema, ensure_ascii=False) + "\n```"
            fb = [*msgs]; i = max(k for k, m in enumerate(fb) if m["role"] == "user")
            fb[i] = {**fb[i], "content": fb[i]["content"] + hint}
            # json_object ép mọi lượt là JSON, kể cả lượt model muốn gọi tool → có tool thì không ép; runner chốt JSON sau
            data = self._post_cacheable({**base, "messages": fb, **({} if tools else {"response_format": {"type": "json_object"}})})
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or "stop"
        if finish == "content_filter":
            raise Refused("model từ chối (content_filter)")
        if finish == "length" and not (choice.get("message") or {}).get("tool_calls"):
            # Hết hạn mức đầu ra là chế độ hỏng RIÊNG, phải nói rõ. Trước đây lượt này lọt xuống dưới với
            # `text=""` (hoặc JSON cụt), rồi runner báo "đầu ra không phải JSON" — người đọc đi sửa prompt
            # trong khi việc cần làm chỉ là tăng `max_tokens`. Model "thinking" đặc biệt dễ dính: token suy
            # nghĩ tính vào cùng hạn mức, có lượt tiêu sạch mà chưa kịp trả lời câu nào.
            u = data.get("usage") or {}
            think = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
            got = len((choice.get("message") or {}).get("content") or "")
            raise LLMError(
                f"model hết hạn mức đầu ra (finish_reason=length): max_tokens={self.cfg.max_tokens}, "
                f"đã sinh {u.get('completion_tokens', '?')} token"
                + (f" (trong đó {think} token suy nghĩ)" if think else "")
                + f", nội dung trả về {got} ký tự"
                + (" — RỖNG, model nghĩ hết hạn mức mà chưa trả lời" if not got else " và bị cắt giữa chừng")
                + f". Tăng `max_tokens` trong llm.yaml (đang {self.cfg.max_tokens}) hoặc hạ `effort` cho tier này."
            )
        calls: list[ToolCall] = []
        for tc in (choice.get("message") or {}).get("tool_calls") or []:
            fn = tc.get("function") or {}
            try: args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError: args = {"_raw": fn.get("arguments")}
            calls.append(ToolCall(id=tc.get("id") or f"call_{len(calls)}", name=fn.get("name", ""), args=args))
        usage = data.get("usage") or {}
        msg = choice.get("message") or {}
        if not calls and not (msg.get("content") or "").strip():
            # Thân rỗng mà HTTP 200 là chế độ hỏng không tự khai báo: cả hai bên đều tưởng bình thường. Trước đây
            # lượt này trả `text=""` xuống runner, `json.loads("")` hỏng và báo "đầu ra không phải JSON" — dẫn
            # người đọc đi sửa schema/prompt, trong khi model có thể đã trả lời đủ.
            #
            # Nguyên nhân THẬT tìm được khi chạy thật (2026-09-04), sau khi đo bằng phép thử đối chứng: server
            # OpenAI-compatible không hiện thực `response_format: json_schema` theo chuẩn mà trả JSON qua
            # `tool_calls` (Google Code Assist hiện thực structured output bằng function call). Client đọc
            # `message.content` thấy rỗng, còn dữ liệu nằm nguyên trong `tool_calls[0].function.arguments`.
            # Vì là 200 chứ không phải lỗi, `_json_schema_ok` vẫn True và mọi lượt sau đều hỏng y hệt.
            think = len(str(msg.get("reasoning_content") or ""))
            raise TransientError(
                f"model không trả về nội dung nào (finish_reason={finish}"
                + (f", có {think} ký tự suy nghĩ" if think else "")
                + "). Hay gặp khi server không hiện thực `response_format: json_schema` đúng chuẩn — kiểm bằng"
                + " cách gọi lại cùng payload với `json_object`; ra nội dung thì lỗi nằm ở tầng structured output."
            )
        # OpenAI-compatible: `prompt_tokens` ĐÃ gồm phần cache, nên `cached_tokens` chỉ để báo cáo, không cộng thêm.
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        return Completion(text=msg.get("content") or "",
                          input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)),
                          model=data.get("model", model), stop_reason=finish, cached_input_tokens=cached, tool_calls=calls)



# ---------- provider CLI: giới hạn argv ----------

# ---------- provider: Claude Code CLI (gói Claude Pro/Max đã đăng nhập trên máy, không cần API key) ----------

# ---------- provider claude-code: chế độ tool CLI ----------

# Tool công ty (tools.py) → tool sẵn có của Claude Code. `run` chỉ thành Bash khi cấu hình có `cli_bash`,
# vì Bash không giới hạn mẫu là mất hẳn allowlist argv của tools.py.
CLI_TOOL_MAP = {"read_file": ["Read"], "list_files": ["Glob"], "search": ["Grep"], "write_file": ["Edit", "Write"]}

# Deny-list file bí mật cho `--settings`: bù phần `_is_secret` của tools.py mà --restricted không làm
# (--restricted khoá tool trong workdir, nhưng .env/khoá riêng NẰM TRONG worktree vẫn đọc được).
CLI_DENY_GLOBS = ("**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/*.p12", "**/*.pfx", "**/*.keystore",
                  "**/id_rsa*", "**/.netrc", "**/.npmrc", "**/.pypirc", "**/.git-credentials",
                  "**/*secret*", "**/*credential*", "**/llm.yaml", "**/.aws/**", "**/.kube/**", "**/.docker/**")


def cli_exit_error(code: int, stdout: str, stderr: str) -> LLMError:
    """Lỗi cho `claude -p` thoát mã ≠ 0: phân loại tạm thời/hẳn theo CHÍNH thông điệp lỗi, không theo đuôi output.

    CLI in một JSON kết quả rồi mới thoát mã 1; đuôi JSON đó là telemetry (`"refused":{"depth_limit":0,
    "concurrency_limit":0,...}`) nên soi 500 ký tự cuối tìm "limit" là MỌI lần thoát mã 1 đều thành "hết quota":
    routing cho backend nghỉ, tick sau thử lại, lặp mãi với cùng một lỗi thật không ai đọc được. Đo được
    (2026-09-05): 20 phút `TransientError: hết quota` mỗi 44s trong khi `claude -p` gọi tay chạy bình thường.
    Đọc JSON: `result`/`error` là thông điệp, `api_error_status` là mã HTTP; không có JSON thì mới dùng stderr."""
    data: dict[str, Any] = {}
    if "{" in stdout:
        try:
            parsed = json.loads(stdout[stdout.index("{"):])
            if isinstance(parsed, dict): data = parsed
        except json.JSONDecodeError:
            data = {}
    if data:
        msg = str(data.get("result") or data.get("error") or "")[:300]
        status = data.get("api_error_status")
        head = (f"claude -p thoát mã {code} (subtype={data.get('subtype') or '?'}, api_error_status={status}): "
                f"{msg or '(không có thông điệp)'}")
        if status in (429, 502, 503, 529) or any(s in msg.lower() for s in ("limit", "rate", "overloaded", "quota")):
            return TransientError(head)
        return LLMError(head)
    err = (stderr or stdout)[-500:]
    if any(s in err.lower() for s in ("limit", "rate", "overloaded", "529", "503")):
        return TransientError(f"claude -p thoát mã {code}: {err}")
    return LLMError(f"claude -p thoát mã {code}: {err}")


# ---------- K3.3c3 bước 2: phần CHUNG của ClaudeCodeClient ----------
#
# Đây KHÔNG phải bản hợp nhất cả lớp, và đó là quyết định có chủ đích. Đo từng method (difflib sau c3 bước 1):
#
#     _parse       0.82  (28 dòng trùng nguyên văn)      -> lên core
#     _subprocess  0.64                                   -> lên core
#     __init__     khác đúng một dòng cuối                -> lên core
#     complete     0.20                                   -> Ở LẠI mỗi công ty
#
# `complete` lệch 0.20 vì ba chiến lược tool KHÁC NHAU THẬT, không phải một bên chậm tiến:
#   studio      — uỷ quyền web tool sẵn có của CLI (`--tools WebFetch,WebSearch`, ADR-0007)
#   company cli — uỷ quyền file/bash tool của CLI ngay trong worktree khách (ADR-0023)
#   company mcp — đưa ĐÚNG bảng tool của công ty vào CLI qua cầu MCP (ADR-0024, `mcp_bridge.py`)
#
# Gộp ba cái đó vào một `complete()` cần năm móc (`_schema_json`, `_extra_args`, `_tool_args`, `_exit_error`,
# `_run_delegated`) để GIẤU một khác biệt có thật — đúng thứ `xagents_core/tools.py` đã từ chối làm cho
# `tools_prompt`: "gộp lại là thêm một tham số mà một bên không bao giờ dùng". Nên core giữ **transport**
# (dựng tiến trình, đọc JSON, kế toán token, phân loại lỗi); **chính sách tool** ở lại nơi nó thuộc về.
#
# Hợp nhất HAI CHIỀU, không bên nào là gốc — như `reported_model` ở K3.3a:
#   company nâng studio: `TransientError` khi timeout và khi `is_error` nhắc quota; `cli_exit_error` đọc JSON
#     thay vì soi 500 ký tự cuối (đo 2026-09-05: soi đuôi biến MỌI lần thoát mã 1 thành "hết quota" — routing
#     cho backend nghỉ rồi thử lại mỗi 44s suốt 20 phút, trong khi `claude -p` gọi tay chạy bình thường);
#     `cache_write_tokens`; `tool_mode`; tham số `cwd`.
#   studio nâng company: bắt `OSError` — "argv quá dài, không có quyền chạy, pipe vỡ". Company KHÔNG bắt, nên
#     một OSError thoát ra ngoài dưới dạng exception thô mà không lớp nào phân loại được.


class ClaudeCodeClient:
    """Transport dùng chung cho `claude -p --output-format json`: dựng tiến trình con, đọc JSON trả về, kế toán
    token, phân loại lỗi. **Chính sách tool không ở đây** — mỗi công ty ghi đè `complete()` của mình.

    Lớp con PHẢI hiện thực `complete()`. Lớp này cố ý không có bản mặc định: một bản "không tool" mặc định sẽ
    im lặng nuốt mất chiến lược tool của bên nào quên ghi đè, mà im lặng đúng là thứ TRAPS.md §1 cấm."""

    def __init__(self, cfg: LLMConfig, binary: str = "claude", timeout: float = 900.0,
                 runner: Callable[..., str] | None = None):   # (args, stdin) hoặc (args, stdin, cwd) khi cli_tools
        self.cfg = cfg
        self.binary = shutil.which(self.cfg.binary or binary) or self.cfg.binary or binary
        self.timeout = timeout
        # Env cho `claude -p`: bỏ khoá của công ty và mọi biến trông như bí mật (ở chế độ cli_tools + cli_bash, Bash của
        # CLI kế thừa env này). Giữ ANTHROPIC_*/CLAUDE_* vì CLI có thể cần chúng để đăng nhập/chọn endpoint.
        self.env = cli_env(keep_prefixes=("ANTHROPIC_", "CLAUDE_"))
        if self.cfg.config_dir:   # nhiều tài khoản Claude trên một máy: mỗi backend một thư mục đăng nhập riêng
            self.env["CLAUDE_CONFIG_DIR"] = str(Path(self.cfg.config_dir).expanduser())
        self._run = runner or self._subprocess  # test thay bằng hàm giả (args, stdin) → stdout

    def _subprocess(self, args: list[str], stdin: str, cwd: str | None = None) -> str:
        import subprocess
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               input=stdin, timeout=self.timeout, env=self.env, cwd=cwd)
        except FileNotFoundError as e:
            raise LLMError(f"không tìm thấy `{self.binary}` (cài Claude Code hoặc đổi provider)") from e
        except subprocess.TimeoutExpired as e:
            raise TransientError(f"claude -p quá {self.timeout}s") from e
        except OSError as e:   # argv quá dài, không có quyền chạy, pipe vỡ… (bản studio bắt, company thì không)
            raise LLMError(f"không chạy được `{self.binary}`: {e}") from e
        if r.returncode != 0:
            raise cli_exit_error(r.returncode, r.stdout or "", r.stderr or "")
        return r.stdout

    def _parse(self, out: str, model: str, tool_mode: str = "") -> Completion:
        """JSON của `claude -p` → Completion (dùng chung cho cả ba chế độ tool)."""
        try:
            data = json.loads(out[out.index("{"):]) if "{" in out else {}
        except json.JSONDecodeError as e:
            raise LLMError(f"claude -p trả về không phải JSON: {out[:300]}") from e
        # `data` luôn là dict: chuỗi được cắt từ dấu `{` đầu tiên nên json.loads chỉ ra object hoặc ném lỗi;
        # nhánh "không phải object JSON" trước đây là code chết, đã bỏ.
        subtype = str(data.get("subtype") or "")
        if subtype in CLI_SUBTYPE_ERRORS:   # đọc TRƯỚC `result`: các subtype này có thể không có result
            raise LLMError(f"claude -p {subtype}: {CLI_SUBTYPE_ERRORS[subtype]}; {str(data.get('result') or '')[:200]}")
        if "result" not in data:
            raise LLMError(f"claude -p thiếu trường result (subtype={subtype or '?'}): {out[:300]}")
        if data.get("is_error"):
            msg = str(data.get("result"))[:300]
            if any(s in msg.lower() for s in ("limit", "rate", "overloaded", "quota")):
                raise TransientError(f"claude -p lỗi: {msg}")
            raise LLMError(f"claude -p lỗi: {msg}")
        if data.get("stop_reason") == "refusal":
            raise Refused("model từ chối")
        u = data.get("usage") or {}
        read = int(u.get("cache_read_input_tokens", 0) or 0); write = int(u.get("cache_creation_input_tokens", 0) or 0)
        used = reported_model(data.get("modelUsage") or {}, model)
        # `--json-schema` → `structured_output` đã parse và đã qua kiểm của CLI: ưu tiên nó, `result` chỉ là bản chữ.
        so = data.get("structured_output")
        text = json.dumps(so, ensure_ascii=False) if isinstance(so, dict) else str(data["result"])
        return Completion(text=text, input_tokens=int(u.get("input_tokens", 0) or 0) + read + write,
                          output_tokens=int(u.get("output_tokens", 0) or 0), model=used,
                          stop_reason=str(data.get("stop_reason") or "end_turn"), cached_input_tokens=read,
                          cache_write_tokens=write, tool_mode=tool_mode)
