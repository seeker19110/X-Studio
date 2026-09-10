"""Ghi / phát lại eval prompt, dùng chung hai công ty (K3.6c của ADR gốc 0001).

**Hình dạng lệch: gần trùng ở cơ chế, khác hẳn ở chính sách.** `difflib` trên cả file cho **0.556** — cao nhất
trong bốn module của K3.6 — nhưng con số gộp ấy giấu mất chuyện đáng kể. Đo TỪNG symbol:

| symbol | difflib | | symbol | difflib |
|---|---|---|---|---|
| `prompt_key`, `recording_path`, `load_recording`, `load_cases`, `_get` | **1.00** | | `check` | 0.84 |
| `outdated_versions` | 0.99 | | `stale_recordings` | 0.83 |
| `_lines` | 0.98 | | `main` | **0.62** |
| `required_agents` | 0.97 | | `CaseResult` | 0.52 |
| `_Probe` | 0.91 | | `ReplayClient` | 0.33 |
| `run_eval` | 0.84 | | `RecordingClient` | 0.29 |
| | | | `_run_case` | **0.14** |

Nửa trên là **cùng một mã**, chép hai lần. Nửa dưới lệch vì ba lý do khác nhau, và chỉ một trong ba là "một
bên đi xa hơn":

1. **`RecordingClient` (0.29) — hợp nhất HAI CHIỀU.** Company có hai thứ studio không có, cả hai là bài học từ
   sự cố thật ngày 2026-09-05 (chốt `prompt_version` lúc `__init__`; `save()` gộp thay vì ghi đè). Studio có
   một thứ company không có: `if not c.tool_calls` — chỉ ghi câu trả lời CUỐI, không ghi lượt gọi tool. Lấy
   bản company là mất cái thứ ba. Core giữ cả ba.
2. **`_run_case` (0.14) — miền, không phải cơ chế.** Company có `phase` (ADR-0037), studio có `many` +
   `extra`. Không có phần chung nào đáng gộp; nó ở lại từng công ty như một **hook**.
3. **`main` (0.62) và `CaseResult` (0.52) — CHÍNH SÁCH CỔNG, không phải cơ chế.** Đây là chỗ dễ sai nhất của
   bước này. Hai bên quyết định "cái gì làm CI đỏ" khác nhau: company tách `gate_ok` (bản ghi) khỏi `cases_ok`
   (điểm chấm) và chỉ đỏ vì điểm khi có `--fail-on-score`; studio đỏ khi **bất kỳ** ca nào không chạy được.
   Lấy `main` của company là **âm thầm nới lỏng cổng của studio**. Nên `main` ở lại từng công ty, và
   `CaseResult` của core mang **hai sự thật có tên** thay vì một cờ:

   - `broken_recording` — hỏng vì BẢN GHI (thiếu, hoặc lệch prompt hiện tại). Company gác cổng bằng cái này.
   - `errored` — ca KHÔNG CHẠY ĐƯỢC vì bất cứ lý do gì (gồm cả trên). Studio gác cổng bằng cái này.

   Hai trường, hai chính sách, không bên nào mất gì. Nếu gộp thành một cờ thì một trong hai công ty phải đổi
   ý nghĩa cổng của mình — mà cổng thì không được đổi kèm theo một PR chuyển mã.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from .llm import Completion, LLMError, ModelClient
from .tools import ToolSpec

__all__ = ["CaseResult", "EvalSuite", "RecordingClient", "ReplayClient", "ScoredOutcome", "Threshold",
           "check", "check_thresholds", "load_recording_score", "load_thresholds", "prompt_key"]

REQUIRED_NAME = "REQUIRED.txt"  # agent BẮT BUỘC có bản ghi tươi; thiếu hoặc lệch phiên bản prompt → CI đỏ


def prompt_key(system: str, user: str) -> str:
    return hashlib.sha256(json.dumps([system, user], ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


def _get(d: Any, dotted: str) -> Any:
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, list) and part.isdigit(): cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict): cur = cur.get(part)
        else: return None
        if cur is None: return None
    return cur


def check(payload: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    """Chấm một payload theo tiêu chí xác định. Không gọi model, không phụ thuộc công ty nào."""
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
    for nhanh in (expect.get("any_of") or []):
        con = [check(payload, alt) for alt in nhanh]
        if all(c for c in con):  # mọi nhánh đều hỏng → báo lý do của TỪNG nhánh, không chỉ nhánh đầu
            fails.append("không nhánh nào của any_of đạt: " + " | ".join("; ".join(c) for c in con))
    return fails


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    tokens: int = 0
    # HAI sự thật có tên, không phải một cờ — xem docstring module, quyết định 3.
    # `broken_recording`: hỏng vì bản ghi (thiếu / lệch prompt). Cổng của company.
    broken_recording: bool = False
    # `errored`: ca không chạy được vì BẤT KỲ lý do gì (gồm cả trên: model lỗi, đầu ra sai schema). Cổng của studio.
    errored: bool = False
    # Số lần ca này được chạy trong lượt vừa rồi, và tỉ lệ đạt trên số lần ấy (p3.3b). `runs=1` là mặc định và
    # là mọi thứ `--replay` làm được: replay TẤT ĐỊNH (khoá = hash(system, user), giá trị = `text` đã ghi) nên
    # chạy lại 100 lần ra đúng một số. Dao động sinh ra lúc GHI, nên n-lần chỉ có nghĩa với `--record`.
    runs: int = 1
    pass_rate: float = 1.0


def load_recording_score(rec: dict[str, Any], key: str) -> float | None:
    """Điểm đã ghi cho một khoá prompt, hoặc `None` khi bản ghi không có (mọi file trước p3.3b).

    Hai trường `score`/`runs` là TUỲ CHỌN có chủ đích: 20 file bản ghi đang nằm trên đĩa không có chúng, và
    một schema bắt buộc là 20 file phải ghi lại bằng model thật trước khi CI xanh trở lại. `None` nghĩa là
    "chưa đo", không phải "điểm 0"."""
    e = (rec.get("cases") or {}).get(key)
    v = e.get("score") if isinstance(e, dict) else None
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


class _Probe:
    """Client giả chỉ để lấy khoá prompt của một ca, không trả lời."""
    key: str | None = None

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        self.key = prompt_key(system, user)
        raise LLMError("probe")


class RecordingClient:
    """Bọc client thật; mỗi phản hồi được lưu theo khoá prompt để phát lại sau.

    Ba tính chất, mỗi cái là một bài học đã trả giá — hai của company, một của studio (docstring module §1)."""

    def __init__(self, inner: ModelClient, agent_id: str, suite: EvalSuite, runs: int = 1):
        self.inner, self.agent_id, self.suite = inner, agent_id, suite
        self.entries: dict[str, dict[str, Any]] = {}
        # `runs` là SỐ LẦN CHẠY MỖI CA của lượt ghi này (p3.3b), không phải số lần gọi `complete`: một ca có
        # tool đi qua nhiều lượt `complete`. `run_eval` đọc thẳng `self.runs` thay vì nhận một tham số riêng —
        # hai nguồn sự thật cho cùng một con số là chỗ để chúng lệch nhau mà không ai thấy.
        self.runs = runs
        self._case_keys: set[str] = set()
        # Chốt phiên bản NGAY LÚC BẮT ĐẦU, không đọc lại lúc `save()`. Một lượt ghi kéo dài nhiều phút; file
        # prompt đổi giữa chừng (người sửa tiếp, hay `git stash`/`checkout` ở nhánh khác) thì bản ghi mang một
        # phiên bản mà nó KHÔNG được ghi bằng — `outdated_versions` đỏ mà không ai hiểu vì sao. Đo được
        # 2026-09-05: stash file prompt trong lúc `make eval-record` chạy, bản ghi ra v11 trong khi prompt v12.
        self.prompt_version = suite.load_agents()[agent_id].version

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        c = self.inner.complete(system=system, user=user, schema=schema, model_tier=model_tier, cache_key=cache_key,
                                tools=tools, messages=messages, workdir=workdir)
        if not c.tool_calls:  # chỉ lưu câu trả lời cuối; lượt gọi tool không ghi (phát lại bỏ qua tool)
            k = prompt_key(system, user)
            self.entries[k] = {"text": c.text, "model": c.model, "input_tokens": c.input_tokens,
                               "output_tokens": c.output_tokens}
            self._case_keys.add(k)
        return c

    # ----- điểm của một ca (p3.3b). `run_eval` gọi hai hàm này quanh N lần chạy của CÙNG một ca -----

    def begin_case(self) -> None:
        self._case_keys = set()

    def end_case(self, pass_rate: float) -> None:
        """Gắn điểm vào MỌI khoá ca vừa sinh ra. `text` được giữ là của lần chạy CUỐI (N lần cùng
        `system`+`user` nên cùng khoá, lần sau đè lần trước) trong khi `score` là tỉ lệ đạt trên cả N —
        `score` là số đo về ĐỘ ỔN ĐỊNH lúc ghi, không phải nhãn pass/fail của đúng câu trả lời được lưu.
        Phát lại vẫn tất định và vẫn chấm chính câu trả lời ấy; `score` không thay `check`."""
        for k in self._case_keys:
            self.entries[k]["score"] = pass_rate
            self.entries[k]["runs"] = self.runs

    def save(self, *, prune_to: set[str] | None = None) -> Path:
        """Gộp vào bản ghi cũ, KHÔNG ghi đè cả file.

        Một ca lỗi giữa chừng (model từ chối, mạng đứt, hết hạn mức) thì lượt ghi chỉ có phần ca chạy được.
        Ghi đè lúc đó xoá luôn những ca đang tốt, và lần replay sau báo "bản ghi lệch prompt" cho một ca mà
        chẳng ai đụng tới — mất bằng chứng vì một sự cố không liên quan. Đo được 2026-09-05 trên qa-debugger.

        Khoá cũ không còn khớp prompt hiện tại thì nằm lại vô hại: `stale_recordings` chấm theo việc khoá
        HIỆN TẠI có mặt hay không, nên rác cũ không che được tín hiệu "phải ghi lại".

        `prune_to` là ngoại lệ CÓ KIỂM SOÁT của quy tắc trên (p3.3c): rác vô hại thì vô hại, nhưng nó tích tụ —
        `product.json` có 32 khoá cho 16 ca. Chỉ khoá trong tập được giữ. Người gọi phải tự chịu trách nhiệm
        rằng tập ấy ĐỦ: truyền vào tập khoá của một lượt chạy thiếu ca là xoá bằng chứng của ca không chạy.
        `None` (mặc định) giữ nguyên hành vi gộp."""
        cu = self.suite.load_recording(self.agent_id) or {}
        cases = {**(cu.get("cases") or {}), **self.entries}
        if prune_to is not None:
            cases = {k: v for k, v in cases.items() if k in prune_to}
        data = {"agent": self.agent_id, "prompt_version": self.prompt_version,
                "recorded_at": datetime.now(UTC).isoformat(),
                "models": sorted({e["model"] for e in cases.values()}), "cases": cases}
        self.suite.recordings_dir.mkdir(parents=True, exist_ok=True)
        p = self.suite.recording_path(self.agent_id)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return p


class ReplayClient:
    """Trả phản hồi đã ghi; prompt không có trong bản ghi (prompt/skill/ca eval đã đổi) → LLMError nói rõ phải ghi lại."""

    def __init__(self, agent_id: str, suite: EvalSuite):
        self.agent_id = agent_id
        data = suite.load_recording(agent_id)
        if data is None:
            raise LLMError(f"chưa có bản ghi eval cho {agent_id}: chạy `make eval-record AGENT={agent_id}` với model thật")
        self.data: dict[str, Any] = data

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        e = self.data["cases"].get(prompt_key(system, user))
        if e is None:
            raise LLMError(f"bản ghi eval của {self.agent_id} lệch prompt hiện tại (prompt/skill/ca eval đã đổi): "
                           f"chạy `make eval-record AGENT={self.agent_id}` với model thật rồi commit bản ghi")
        return Completion(text=e["text"], input_tokens=int(e.get("input_tokens", 0)),
                          output_tokens=int(e.get("output_tokens", 0)), model=f"replay:{e.get('model', '?')}")


class EvalSuite:
    """Bộ eval của một công ty. Cơ chế ở đây; bốn thứ dưới do lớp con cung cấp.

    `run_case` là hook lớn nhất: nó biết `phase` (company) hay `many`/`extra` (studio) — miền, không phải cơ
    chế (docstring module §2). `main` KHÔNG ở đây: nó là chính sách cổng của từng công ty (§3)."""

    #: Lỗi ĐƯỢC PHÉP biến một ca thành `CaseResult(errored=True)` thay vì thoát ra ngoài. Lớp con đặt
    #: `(RunnerError, LLMError)`. Để `Exception` ở đây là nuốt cả lỗi lập trình — một `KeyError` trong
    #: `run_case` sẽ hiện ra như "model trả sai", và eval báo FAIL cho một ca mà nguyên nhân nằm ở code.
    case_errors: tuple[type[BaseException], ...] = (LLMError,)

    def __init__(self, root: Path):
        self.root = root

    # ---------- lớp con cung cấp ----------

    def load_agents(self) -> dict[str, Any]:
        raise NotImplementedError

    def new_bus(self) -> Any:
        raise NotImplementedError

    def new_blackboard(self, bus: Any) -> Any:
        raise NotImplementedError

    def run_case(self, agent_id: str, case: dict[str, Any], client: ModelClient,
                 agents: dict[str, Any] | None, bb: Any, bus: Any) -> Any:
        raise NotImplementedError

    # ---------- đường dẫn ----------

    @property
    def evals_dir(self) -> Path:
        return self.root / "evals"

    @property
    def recordings_dir(self) -> Path:
        return self.evals_dir / "recordings"

    def recording_path(self, agent_id: str) -> Path:
        return self.recordings_dir / f"{agent_id}.json"

    def load_recording(self, agent_id: str) -> dict[str, Any] | None:
        p = self.recording_path(agent_id)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def load_cases(self, agent_id: str) -> list[dict[str, Any]]:
        p = self.evals_dir / f"{agent_id}.yaml"
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("cases", []) if p.exists() else []

    # ---------- cổng bản ghi ----------

    def required_agents(self) -> list[str]:
        """Agent phải có bản ghi eval tươi. Thêm id vào `evals/recordings/REQUIRED.txt` ngay khi commit bản ghi
        đầu tiên của agent đó; từ lúc ấy CI đỏ nếu bản ghi biến mất hoặc lệch prompt (ADR-0004, ADR-0010)."""
        p = self.recordings_dir / REQUIRED_NAME
        if not p.exists(): return []
        return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]

    def outdated_versions(self, agent_ids: list[str] | None = None) -> dict[str, str]:
        """Bản ghi ghi bằng phiên bản prompt cũ hơn phiên bản hiện tại → {agent: "ghi v3, hiện v5"}.
        Kiểm được offline, không cần gọi model: đây là răng của "đổi prompt phải chạy lại eval"."""
        agents = self.load_agents(); out: dict[str, str] = {}
        for aid in agent_ids or sorted(agents):
            rec = self.load_recording(aid)
            if rec is None: continue
            got, want = int(rec.get("prompt_version", 0)), agents[aid].version
            if got != want: out[aid] = f"bản ghi ở prompt v{got}, agent hiện v{want}"
        return out

    def stale_recordings(self, agent_ids: list[str] | None = None) -> dict[str, list[str]]:
        """Bản ghi hiện có mà thiếu khoá cho ca eval hiện tại → {agent: [tên ca]} (rỗng = mọi bản ghi còn khớp).
        Test dùng hàm này để CI đỏ ngay khi prompt đổi mà chưa chạy lại eval bằng model thật."""
        agents = self.load_agents(); out: dict[str, list[str]] = {}
        for aid in agent_ids or sorted(agents):
            rec = self.load_recording(aid)
            if rec is None: continue
            missing = []
            for case in self.load_cases(aid):
                bus, probe = self.new_bus(), _Probe()
                bb = self._with_context(self.new_blackboard(bus), case)
                try: self.run_case(aid, case, probe, agents, bb, bus)
                except self.case_errors: pass   # `_Probe` LUÔN ném LLMError sau khi ghi lại khoá
                if probe.key is not None and probe.key not in rec["cases"]: missing.append(case["name"])
            if missing: out[aid] = missing
        return out

    # ---------- chạy ----------

    def _with_context(self, bb: Any, case: dict[str, Any]) -> Any:
        for ctx in case.get("context", []):
            bb.write(ctx["actor"], ctx["namespace"], ctx["content_ref"], ctx.get("summary", ""),
                     content=ctx.get("content"))
        return bb

    def _run_once(self, agent_id: str, case: dict[str, Any], client: ModelClient,
                  agents: dict[str, Any] | None) -> CaseResult:
        bus = self.new_bus()
        bb = self._with_context(self.new_blackboard(bus), case)
        try:
            r = self.run_case(agent_id, case, client, agents, bb, bus)
        except self.case_errors as e:
            # Nhận diện bản ghi lệch bằng SUBSTRING tiếng Việt của thông điệp `ReplayClient` — đổi chữ ở đó là
            # gãy im lặng (cổng của company thôi đỏ mà không ca nào báo). Giữ nguyên chữ, hoặc đổi cả hai chỗ.
            broken = isinstance(e, LLMError) and "bản ghi" in str(e)
            return CaseResult(case["name"], False, [str(e)], broken_recording=broken, errored=True, pass_rate=0.0)
        fails = check(r.output.payload, case.get("expect", {}))
        return CaseResult(case["name"], not fails, fails, r.tokens, pass_rate=0.0 if fails else 1.0)

    def run_eval(self, agent_id: str, client: ModelClient, agents: dict[str, Any] | None = None,
                 runs: int = 1) -> list[CaseResult]:
        """`runs > 1` chỉ có nghĩa khi GHI: replay tất định nên chạy lại chỉ tốn thời gian (p3.3b). Với
        `RecordingClient`, số lần lấy thẳng từ client — một con số, một nguồn. `runs < 1` là lỗi của người gọi
        (ZeroDivisionError); CLI chặn trước bằng `--runs`.

        Kết quả gộp lấy lần chạy HỎNG đầu tiên làm đại diện (nên `passed` chỉ đúng khi cả N lần đạt — cùng
        nghĩa với hôm nay ở `runs=1`), `tokens` cộng cả N lần, `pass_rate` là tỉ lệ đạt."""
        rec = client if isinstance(client, RecordingClient) else None
        runs = rec.runs if rec is not None else runs
        results: list[CaseResult] = []
        for case in self.load_cases(agent_id):
            if rec is not None: rec.begin_case()
            lan = [self._run_once(agent_id, case, client, agents) for _ in range(runs)]
            rate = sum(r.passed for r in lan) / len(lan)
            if rec is not None: rec.end_case(rate)
            xau = [r for r in lan if not r.passed]
            goc = xau[0] if xau else lan[0]
            results.append(replace(goc, tokens=sum(r.tokens for r in lan), runs=runs, pass_rate=rate))
        return results

    @staticmethod
    def lines(agent_id: str, res: list[CaseResult]) -> list[str]:
        out = [f"{'PASS' if r.passed else 'FAIL'} {agent_id}/{r.name} ({r.tokens} tok)"
               + "".join(f"\n   - {f}" for f in r.failures) for r in res]
        return [*out, f"{agent_id}: {sum(r.passed for r in res)}/{len(res)} pass"]


# ---------- Cổng điểm eval (4L-1a của company, p3.3 mở cho cả studio) ----------
#
# Cơ chế ở core, NGHĨA ở từng công ty (ADR-0001). Cụ thể: core không biết `evals/thresholds.yaml` nằm ở đâu —
# đường dẫn do người gọi truyền vào; core cũng không biết một công ty gác cổng bằng cái gì (company tách
# `gate_ok`/`cases_ok`, studio dùng một cờ) — `check_thresholds` chỉ TRẢ VỀ dòng FAIL, ai gọi thì tự quyết mã thoát.


class ScoredOutcome(Protocol):
    """Thứ tối thiểu `check_thresholds` cần biết về kết quả một agent.

    Cố ý là Protocol chứ không phải một dataclass của core: `company._AgentOutcome` còn mang `gate_ok` và
    `cases_ok` — chính sách cổng RIÊNG của company. Đưa chúng lên core là đưa nghĩa của một công ty vào lõi
    chung; bắt company đổi sang một dataclass của core là buộc nó bỏ hai trường ấy hoặc thêm một lớp chuyển
    đổi vô ích. Duck-typed thì cả hai công ty giữ nguyên kiểu của mình và core không biết gì thừa."""

    @property
    def agent_id(self) -> str: ...  # pragma: no cover

    @property
    def total(self) -> int: ...  # pragma: no cover

    @property
    def passed(self) -> int: ...  # pragma: no cover


@dataclass(frozen=True)
class Threshold:
    """Sàn điểm của một agent. `cases` chống thu nhỏ bộ ca để né `min_pass_ratio`: xoá bớt ca xấu
    làm ratio đẹp lên nhưng `total` tụt dưới `cases` thì vẫn đỏ."""
    min_pass_ratio: float
    cases: int


def load_thresholds(path: Path) -> dict[str, Threshold]:
    """Không có file → `{}` (tính năng không áp, không phải lỗi — agent mới chưa kịp có ngưỡng).
    Có file nhưng sai hình (không phải mapping, thiếu trường) → `LLMError` rõ ràng thay vì KeyError mù mờ.

    `path` BẮT BUỘC: core dùng chung hai công ty nên không được hằng hoá `evals/` của một bên nào."""
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise LLMError(f"{path}: sai cú pháp YAML: {e}") from e
    if not isinstance(raw, dict):
        raise LLMError(f"{path}: phải là mapping agent -> {{min_pass_ratio, cases}}")
    out: dict[str, Threshold] = {}
    for aid, v in raw.items():
        if not isinstance(v, dict) or "min_pass_ratio" not in v or "cases" not in v:
            raise LLMError(f"{path}: {aid} thiếu `min_pass_ratio` hoặc `cases`")
        try:
            out[aid] = Threshold(min_pass_ratio=float(v["min_pass_ratio"]), cases=int(v["cases"]))
        except (TypeError, ValueError) as e:
            raise LLMError(f"{path}: {aid} có `min_pass_ratio`/`cases` không phải số: {e}") from e
    return out


def check_thresholds(outcomes: Iterable[ScoredOutcome], th: dict[str, Threshold]) -> list[str]:
    """Dòng FAIL cho agent tụt dưới sàn. Tính SAU khi mọi outcome đã gom xong (dùng được với `--jobs`).
    Agent không có trong `th` → không áp (agent mới, hoặc cố ý chưa đặt ngưỡng); `total == 0` → không áp
    (không có ca eval thì không có gì để chấm, tránh chia 0 và tránh đỏ oan agent chưa có bộ ca)."""
    fails: list[str] = []
    for o in outcomes:
        t = th.get(o.agent_id)
        if t is None or o.total == 0:
            continue
        ratio = o.passed / o.total
        if ratio < t.min_pass_ratio:
            fails.append(f"FAIL {o.agent_id}: điểm {ratio:.2f} dưới ngưỡng {t.min_pass_ratio:.2f} "
                        f"({o.passed}/{o.total} ca) — evals/thresholds.yaml")
        if o.total < t.cases:
            fails.append(f"FAIL {o.agent_id}: bộ ca còn {o.total} dưới ngưỡng {t.cases} ca — "
                        f"bộ ca bị thu nhỏ, evals/thresholds.yaml")
    return fails
