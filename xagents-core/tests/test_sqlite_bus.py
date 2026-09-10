"""`xagents_core.sqlite_bus` — bus bền vững chung (K3.5c).

Dùng lại đúng CÔNG TY GIẢ của `test_bus.py` (hai topic, một `Envelope` con): mã ở core thì ca ở core, và ca ở
core không được mượn tên topic của company hay studio. Bốn ca cuối (`__del__`, `_alive` hai nền tảng, file
lock hỏng) chuyển sang từ `software-company/tests/test_coverage_100.py` cùng với mã chúng đo.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import types

import pytest

from conftest import FakeEnvelope, _tin
from xagents_core import sqlite_bus as SB
from xagents_core.bus import BusError
from xagents_core.sqlite_bus import BUSY_TIMEOUT_S, Lease, LeaseError, SQLiteBus


class Bus(SQLiteBus[FakeEnvelope]):
    envelope_cls = FakeEnvelope


@pytest.fixture
def bus(cfg, tmp_path):
    b = Bus(cfg, tmp_path / "b.sqlite")
    yield b
    b.close()


# ---------- ghi, mở lại, replay ----------

def test_path_mac_dinh_lay_tu_cfg_db_name(cfg, tmp_path, monkeypatch):
    """Core không viết tên file bus của công ty nào vào mình: mặc định đến từ `CoreConfig.db_name`."""
    monkeypatch.chdir(tmp_path)
    b = Bus(cfg)
    try:
        assert b.path.name == "fake.sqlite" and b.path.exists()
    finally:
        b.close()


def test_ghi_dia_va_mo_lai_giu_thu_tu(cfg, tmp_path):
    db = tmp_path / "b.sqlite"
    b1 = Bus(cfg, db)
    for i in range(3):
        b1.publish(_tin(key=f"B{i}"))
    b1.close()
    b2 = Bus(cfg, db)
    try:
        assert [e.key for e in b2.replay()] == ["B0", "B1", "B2"]
        assert [e.key for e in b2.replay(topic="ban-tin", key="B1")] == ["B1"]
        assert list(b2.replay(topic="khong-co")) == []
        assert len(b2) == 3
    finally:
        b2.close()


def test_publish_khong_hop_le_thi_khong_ghi_gi(bus):
    with pytest.raises(BusError):
        bus.publish(FakeEnvelope(topic="ban-tin", key="B1", actor="bien-tap", payload={}))
    assert bus._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_latest_lay_ban_moi_nhat_va_none_khi_khong_co(bus):
    bus.publish(_tin(key="B1", payload={"tieu_de": "cu"}))
    bus.publish(_tin(key="B1", payload={"tieu_de": "moi"}))
    got = bus.latest("ban-tin", "B1")
    assert got is not None and got.payload["tieu_de"] == "moi"
    assert bus.latest("ban-tin", "khong-co") is None


# ---------- poll: event của tiến trình khác ----------

def test_poll_bao_event_cua_tien_trinh_khac_va_bo_qua_event_cua_minh(cfg, tmp_path, bus):
    thay: list[FakeEnvelope] = []
    bus.subscribe("ban-tin", thay.append)
    bus.publish(_tin(key="MINH"))
    assert thay == bus._log[:1] and bus.poll() == []      # hàng của chính mình chỉ đẩy con trỏ seq

    khac = Bus(cfg, tmp_path / "b.sqlite")                # "tiến trình khác" ghi vào cùng file
    khac.publish(_tin(key="KHAC")); khac.close()

    moi = bus.poll()
    assert [e.key for e in moi] == ["KHAC"] and [e.key for e in thay] == ["MINH", "KHAC"]


def test_publish_ghi_dia_truoc_khi_handler_nem_loi(cfg, tmp_path):
    """Handler hỏng không được làm mất event: nó đã bền vững trước khi subscriber được báo."""
    db = tmp_path / "b.sqlite"
    b = Bus(cfg, db)
    b.subscribe("ban-tin", lambda e: (_ for _ in ()).throw(RuntimeError("handler hỏng")))
    with pytest.raises(RuntimeError, match="handler hỏng"):
        b.publish(_tin(key="B1"))
    b.close()
    b2 = Bus(cfg, db)
    try:
        keys = [e.key for e in b2.replay(topic="ban-tin")]
        assert keys == ["B1"]                                     # event vẫn còn trên đĩa
        assert [e.payload["action"] for e in b2.replay(topic="audit-log")] == ["subscriber_error"]
    finally:
        b2.close()


def test_persist_only_ghi_dia_ma_khong_bao_subscriber(bus):
    thay: list[FakeEnvelope] = []
    bus.subscribe("*", thay.append)
    bus._persist_only(_tin(key="B1"))
    assert thay == [] and bus._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    bus.publish(_tin(key="B2"))
    assert [e.key for e in thay] == ["B2"]     # bảng subscriber được trả lại nguyên vẹn


def test_bus_dung_duoc_tu_thread_khac(bus):
    """`check_same_thread=False` — bản studio trước K3.5c thiếu cờ này nên nổ `ProgrammingError` ở đây."""
    import threading
    loi: list[Exception] = []

    def ghi():
        try: bus.publish(_tin(key="B1"))
        except Exception as e: loi.append(e)
    t = threading.Thread(target=ghi); t.start(); t.join()
    assert loi == [] and len(bus) == 1


def test_busy_timeout_la_hang_chung():
    assert BUSY_TIMEOUT_S == 30.0


# ---------- close / __del__ ----------

def test_close_dong_ket_noi(cfg, tmp_path):
    b = Bus(cfg, tmp_path / "b.sqlite"); b.close()
    with pytest.raises(sqlite3.ProgrammingError):
        b._db.execute("SELECT 1")


def test_del_nuot_loi_khi_dong_that_bai(bus):
    """`__del__` chạy lúc thông dịch tắt: `close()` hỏng thì phải nuốt, vì `__del__` không được phép ném."""
    that = bus._db   # giữ kết nối THẬT lại: bỏ rơi nó là đúng cái ResourceWarning mà `__del__` sinh ra để tránh

    class _Hong:
        def close(self): raise RuntimeError("thông dịch đang tắt")

    bus._db = _Hong()   # type: ignore[assignment]
    bus.__del__()       # không được ném
    bus._db = that


# ---------- Lease ----------

def test_alive_tren_windows_dung_openprocess(monkeypatch):
    """Nhánh nền tảng rẽ theo `sys.platform`, không phải `os.name` — xem chú thích trong `_alive`: chỉ
    `sys.platform` mới cho mypy thu hẹp, nên chỉ nó mới làm `mypy` sạch trên CẢ Linux lẫn Windows."""
    calls: list[tuple] = []

    class _K32:
        def OpenProcess(self, flags, inherit, pid): calls.append((flags, pid)); return 0 if pid == 404 else 7
        def CloseHandle(self, h): calls.append(("close", h))
    monkeypatch.setattr(SB.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=_K32())))
    assert SB._alive(404) is False           # OpenProcess trả handle rỗng → coi như đã chết
    assert SB._alive(123) is True and ("close", 7) in calls
    assert calls[0] == (0x1000, 404)


def test_alive_permission_error_la_con_song(monkeypatch):
    monkeypatch.setattr(SB.sys, "platform", "linux")

    def kill(pid, sig): raise PermissionError
    monkeypatch.setattr(SB.os, "kill", kill)
    assert SB._alive(1) is True


@pytest.mark.parametrize("pid", [0, 2 ** 22 - 1])
def test_alive_pid_khong_hop_le_hoac_da_chet(pid, monkeypatch):
    monkeypatch.setattr(SB.sys, "platform", "linux")

    def kill(p, sig): raise ProcessLookupError
    monkeypatch.setattr(SB.os, "kill", kill)
    assert SB._alive(pid) is False


def test_alive_nhan_ra_tien_trinh_dang_chay(monkeypatch):
    monkeypatch.setattr(SB.sys, "platform", "linux")
    assert SB._alive(os.getpid()) is True     # `os.kill(pid, 0)` không ném: còn sống


def test_lease_giu_va_nha(tmp_path):
    lease = Lease(tmp_path / "b.sqlite")
    lease.acquire()
    assert lease.held and lease.path.read_text(encoding="utf-8") == str(os.getpid())
    lease.acquire()                     # cùng pid: lấy lại được, không phải lỗi
    lease.release()
    assert not lease.path.exists() and not lease.held
    lease.release()                     # nhả hai lần không nổ


def test_lease_bo_qua_file_lock_hong(tmp_path):
    lease = Lease(tmp_path / "b.sqlite")
    lease.path.write_text("không phải số", encoding="utf-8")
    lease.acquire()          # pid không đọc được → coi như lock cũ, lấy lại được
    assert lease.held and lease.path.read_text(encoding="utf-8") == str(os.getpid())
    lease.release()


def test_lease_tu_choi_khi_tien_trinh_khac_con_song(tmp_path, monkeypatch):
    lease = Lease(tmp_path / "b.sqlite")
    lease.path.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(SB, "_alive", lambda pid: True)
    with pytest.raises(LeaseError, match="đang chạy"):
        lease.acquire()
    assert not lease.held
