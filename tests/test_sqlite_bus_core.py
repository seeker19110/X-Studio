"""K3.5c: `sqlite_bus` lên `xagents_core` — studio nhận bản company, và đó là ĐỔI HÀNH VI.

Ba ca dưới đo đúng ba thứ studio **chưa có** trước bước này. Chúng không đo lại cơ chế (ca cơ chế ở
`xagents-core/tests/test_sqlite_bus.py`); chúng chỉ trả lời câu "cái mới có thật sự tới được studio không",
vì shim `studio/sqlite_bus.py` là chỗ duy nhất có thể ghép sai.
"""
from __future__ import annotations

import sqlite3
import threading

from studio.events import Envelope
from studio.sqlite_bus import SQLiteBus

_BRIEF = {"channel_id": "CH1", "goals": ["g"], "audience": "người mới", "pillars": ["hướng dẫn"],
          "cadence": "2/tuần", "boundaries": ["không hứa thu nhập"]}


def _brief(**kw) -> Envelope:
    return Envelope(topic="channel-briefs", key="CH1", actor="human", payload={**_BRIEF, **kw})


def test_studio_co_latest_tim_tren_index(tmp_path):
    """`latest()` — bản studio trước K3.5c không có, mọi nơi phải `list(replay(...))[-1]`."""
    bus = SQLiteBus(tmp_path / "s.sqlite")
    try:
        bus.publish(_brief(cadence="1/tuần")); moi = bus.publish(_brief(cadence="3/tuần"))
        got = bus.latest("channel-briefs", "CH1")
        assert got is not None and got.event_id == moi.event_id and type(got) is Envelope
        assert bus.latest("channel-briefs", "khong-co") is None
    finally:
        bus.close()


def test_studio_dung_duoc_tu_thread_khac(tmp_path):
    """`check_same_thread=False` + `RLock`: bản studio cũ ném `sqlite3.ProgrammingError` ở đây."""
    bus = SQLiteBus(tmp_path / "s.sqlite")
    loi: list[Exception] = []

    def ghi():
        try: bus.publish(_brief())
        except Exception as e: loi.append(e)
    try:
        t = threading.Thread(target=ghi); t.start(); t.join()
        assert loi == [] and len(bus) == 1
    finally:
        bus.close()


def test_studio_ghi_dia_truoc_khi_handler_nem_loi(tmp_path):
    """Handler hỏng: event vẫn bền vững, và lỗi thành một `audit-log` thay vì biến mất."""
    db = tmp_path / "s.sqlite"
    bus = SQLiteBus(db)
    bus.subscribe("channel-briefs", lambda e: (_ for _ in ()).throw(RuntimeError("vỡ")))
    try:
        bus.publish(_brief())
    except RuntimeError:
        pass
    else:  # pragma: no cover - chỉ để ca này đỏ rõ ràng nếu lỗi bị nuốt
        raise AssertionError("publish phải ném lại lỗi của handler")
    bus.close()

    bus2 = SQLiteBus(db)
    try:
        assert len(list(bus2.replay(topic="channel-briefs"))) == 1
        assert [e.payload["action"] for e in bus2.replay(topic="audit-log")] == ["subscriber_error"]
    finally:
        bus2.close()


def test_close_dong_ket_noi(tmp_path):
    bus = SQLiteBus(tmp_path / "s.sqlite"); bus.close()
    try:
        bus._db.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return
    raise AssertionError("close() phải đóng kết nối")
