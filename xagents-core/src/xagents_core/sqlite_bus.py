"""Bus bền vững trên SQLite, dùng chung hai công ty (K3.5c của ADR gốc 0001).

**Vì sao bước này là "lấy bản company" thật, khác cả K3.5a lẫn K3.5b.** `difflib` hai file cho **0.442** —
cao nhất trong ba module của K3.5, và con số ấy đúng: hai bên cùng một `_DDL`, cùng cách nạp lại `_log` khi mở,
cùng câu `INSERT`, cùng `replay` ghép `WHERE`. Chỗ lệch không phải hai miền (K3.5a) cũng không phải một bên
thiếu cả cơ chế (K3.5b), mà là **company đã đi xa hơn trên cùng một con đường**: khoá, `latest()` để SQLite tìm
trên index, `_persist_only` ghi đĩa, `__del__` đóng kết nối, và `Lease` cho hai tiến trình. Sáu hàm chỉ company
có, một hàm chỉ studio có (`_notify`) — và hàm ấy đã lên core từ K3.5b, nên nó không còn là điểm lệch.

Ba quyết định hợp nhất, mỗi cái kèm cái nó đổi ở studio:

1. **Khoá.** Bản studio không khoá gì cả: `publish` tháo `_subs` ra, gọi `super().publish`, ghi đĩa, rồi báo
   subscriber — ba bước không nguyên tử. Bản company giữ `RLock` của `InMemoryBus` quanh cả `INSERT` + append
   `_log` + báo subscriber. Studio nhận khoá này. Nó cũng là lý do studio **không cần** mẹo tháo `_subs` nữa:
   thứ tự "ghi đĩa TRƯỚC, báo sau" nay nằm thẳng trong `publish` của core.
2. **`check_same_thread=False`.** Bản studio thiếu, nên một `SQLiteBus` truyền sang thread khác là
   `ProgrammingError`. Nó chỉ chưa nổ vì runner studio chạy một thread. Đi kèm khoá ở (1) nên an toàn: nhiều
   thread dùng chung MỘT kết nối, tuần tự hoá bằng `RLock`.
3. **`BUSY_TIMEOUT_S` là hằng chung.** Company viết thẳng `timeout=30` trong lời gọi, studio đặt tên
   `BUSY_TIMEOUT_S = 30.0` rồi giải thích tại sao. Cùng một con số, một bên có tên một bên không — lấy cái có
   tên. Đây là hướng ngược với hai điểm trên, và là chỗ duy nhất bản studio thắng.

`path` mặc định lấy từ `cfg.db_name` (`"company.sqlite"` / `"studio.sqlite"`): core không được viết tên file
của một công ty vào mình (`config.py`).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from .bus import BusError, InMemoryBus
from .config import CoreConfig
from .events import Envelope

__all__ = ["BUSY_TIMEOUT_S", "DDL", "Lease", "LeaseError", "SQLiteBus"]

E = TypeVar("E", bound=Envelope)

DDL = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  topic TEXT NOT NULL, key TEXT NOT NULL, actor TEXT NOT NULL, ts TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_topic_key ON events(topic, key);
"""
#: Nhiều tiến trình (orchestrator + gate CLI) cùng ghi một file → chờ khoá thay vì "database is locked" ngay.
BUSY_TIMEOUT_S = 30.0


class SQLiteBus(InMemoryBus[E]):
    """Cùng interface với `InMemoryBus`, đủ cho một máy. Mọi envelope append vào bảng `events`; mở lại là
    replay được theo topic/key — đây cũng là checkpoint để tiếp tục việc bị gián đoạn đúng chỗ (ADR-0001)."""

    def __init__(self, cfg: CoreConfig, path: str | Path | None = None, enforce_owners: bool = True):
        super().__init__(cfg, enforce_owners=enforce_owners)
        self.path = Path(cfg.db_name if path is None else path)
        # check_same_thread=False + RLock của lớp cha: nhiều thread của orchestrator dùng chung một kết nối, tuần tự hoá.
        # timeout: tiến trình khác (gate CLI, publish) đang ghi thì chờ thay vì "database is locked" ngay.
        # WAL: đọc không chặn ghi giữa các tiến trình (orchestrator watch + CLI cùng một file).
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=BUSY_TIMEOUT_S)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(DDL)
        self._seq = 0  # seq cuối đã ĐỌC từ đĩa (không phải seq mình vừa ghi) — poll không được bỏ sót event của tiến trình khác
        self._seen: set[str] = set()  # event_id đã có trong _log (tự ghi hoặc poll về) — poll không nạp lại
        for seq, body in self._db.execute("SELECT seq, body FROM events ORDER BY seq"):
            env = self.envelope_cls.model_validate_json(body)
            self._log.append(env); self._seen.add(env.event_id); self._seq = seq

    def publish(self, env: E) -> E:
        # Lớp cha validate + kiểm quyền; ghi đĩa TRƯỚC khi vào log bộ nhớ và báo subscriber: handler ném lỗi thì event
        # vẫn đã bền vững. KHÔNG nhảy `_seq` tới lastrowid: tiến trình khác có thể đã chèn hàng có seq nhỏ hơn (giữa
        # hai lần poll) — poll đọc từ `_seq` cũ và bỏ qua hàng đã thấy theo event_id.
        self._check_publish(env)
        with self._lock:
            self._write(env)
            self._log.append(env); self._seen.add(env.event_id)
            self._notify_safely(env, reraise=True)  # như InMemoryBus: mọi subscriber nhận, lỗi ghi audit rồi ném lại
        return env

    def _write(self, env: E) -> None:
        with self._db:
            self._db.execute("INSERT INTO events(event_id, topic, key, actor, ts, body) VALUES (?,?,?,?,?,?)",
                             (env.event_id, env.topic, env.key, env.actor, env.ts.isoformat(), env.model_dump_json()))

    def _persist_only(self, env: E) -> E:
        """Ghi đĩa + log nhưng không báo subscriber: audit về handler hỏng không được đi qua chính handler đó."""
        with self._lock:
            subs, self._subs = self._subs, defaultdict(list)
            try:
                return self.publish(env)
            finally:
                self._subs = subs

    def poll(self) -> list[E]:
        """Nạp event do tiến trình KHÁC ghi vào cùng file (gate CLI, human publish) và báo subscriber như event mới.
        Hàng do chính tiến trình này ghi (đã có trong _seen) chỉ đẩy `_seq` lên, không báo lại."""
        with self._lock:
            rows = self._db.execute("SELECT seq, event_id, body FROM events WHERE seq > ? ORDER BY seq",
                                    (self._seq,)).fetchall()
            new: list[E] = []
            for seq, event_id, body in rows:
                self._seq = seq
                if event_id in self._seen: continue
                env = self.envelope_cls.model_validate_json(body)
                self._log.append(env); self._seen.add(event_id); new.append(env)
                self._notify_safely(env)
        return new

    def replay(self, topic: str | None = None, key: str | None = None) -> Iterable[E]:
        q, args, conds = "SELECT body FROM events", [], []
        if topic: conds.append("topic = ?"); args.append(topic)
        if key: conds.append("key = ?"); args.append(key)
        if conds: q += " WHERE " + " AND ".join(conds)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY seq", args).fetchall()
        for (body,) in rows:
            yield self.envelope_cls.model_validate_json(body)

    def latest(self, topic: str, key: str) -> E | None:
        """Như lớp cha nhưng để SQLite tìm: `ORDER BY seq DESC LIMIT 1` trên index (topic, key), không quét log."""
        with self._lock:
            row = self._db.execute("SELECT body FROM events WHERE topic = ? AND key = ? ORDER BY seq DESC LIMIT 1",
                                   (topic, key)).fetchone()
        return self.envelope_cls.model_validate_json(row[0]) if row else None

    def close(self) -> None:
        self._db.close()

    def __del__(self) -> None:
        """Đóng kết nối khi bus bị thu hồi mà người dùng quên `close()`.

        Không có bước này, `sqlite3.Connection` tự cảnh báo `ResourceWarning: unclosed database` lúc GC — trên
        Python 3.13 và với `filterwarnings = error` thì cảnh báo đó là lỗi test. Đóng ở đây sửa đúng chỗ rò (mỗi
        lần mở lại bus là một kết nối) thay vì tắt cảnh báo đi. Chạy lúc thông dịch đang tắt thì thuộc tính có thể
        đã biến mất, nên bọc rộng."""
        try:
            self._db.close()
        except Exception:   # __del__ không được phép ném; mất kết nối lúc thông dịch tắt là chuyện thường
            pass


class LeaseError(BusError): ...


def _alive(pid: int) -> bool:
    """Tiến trình còn sống? Không dùng os.kill(pid, 0) trên Windows (ở đó nó TerminateProcess)."""
    if pid <= 0: return False
    # `sys.platform` chứ không `os.name`: mypy THU HẸP theo `sys.platform` (PEP 484) nhưng không theo
    # `os.name`. Với `os.name == "nt"`, mypy chạy trên Linux vẫn soi thân khối và đỏ ở `ctypes.windll`
    # (`windll` chỉ tồn tại trên Windows), nên phải chú `# type: ignore[attr-defined]` — mà chú ấy lại THỪA
    # khi mypy chạy trên Windows, và core bật `strict` nên "thừa" cũng là lỗi. Hệ quả trước 2026-09-09:
    # `mypy src/xagents_core` KHÔNG BAO GIỜ xanh được trên cả hai nền tảng cùng lúc, và vì `core-static` chỉ
    # chạy ubuntu nên CI không thấy nửa còn lại. Với `sys.platform` thì trên Linux cả khối là unreachable và
    # trên Windows `windll` có thật — sạch ở cả hai, không cần chú nào.
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return False
        k32.CloseHandle(h); return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lease:
    """Một tiến trình orchestrator cho một file bus: hai tiến trình `run` trên cùng SQLite sẽ xử lý trùng event (processed
    chỉ học qua audit sau poll). File `<db>.lock` giữ pid; pid chết → lock cũ, lấy lại được."""

    def __init__(self, db: str | Path):
        self.path = Path(str(db) + ".lock")
        self.held = False

    def acquire(self) -> None:
        if self.path.exists():
            try: pid = int(self.path.read_text(encoding="utf-8").strip() or "0")
            except (ValueError, OSError): pid = 0
            if pid != os.getpid() and _alive(pid):
                raise LeaseError(f"orchestrator khác (pid {pid}) đang chạy trên {self.path.with_suffix('')}: "
                                 f"dừng nó trước, hoặc xoá {self.path} nếu chắc chắn nó đã chết")
        self.path.write_text(str(os.getpid()), encoding="utf-8"); self.held = True

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True); self.held = False
