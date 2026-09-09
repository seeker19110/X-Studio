"""K3.6b: `blackboard` lên `xagents_core` — studio nhận bản company, và đó là ĐỔI HÀNH VI.

Bốn ca dưới đo bốn thứ studio **chưa có** trước bước này. Chúng không đo lại cơ chế (ca cơ chế ở
`xagents-core/tests/test_blackboard.py`); chúng chỉ trả lời "cái mới có tới được studio không".
"""
from __future__ import annotations

import threading

from studio.blackboard import Blackboard
from studio.bus import InMemoryBus
from studio.sqlite_bus import SQLiteBus


def test_studio_co_rehydrate_dung_lai_tu_bus(tmp_path):
    """Mở lại `studio.sqlite`: bản trước K3.6b để blackboard RỖNG, mọi agent chạy tiếp với ngữ cảnh trắng."""
    db = tmp_path / "s.sqlite"
    bus = SQLiteBus(db)
    bb = Blackboard(bus)
    bb.write("channel-strategist", "strategy", "ch1/strategy.md")
    bb.write("channel-strategist", "strategy", "ch1/strategy-v2.md")
    bus.close()

    bus2 = SQLiteBus(db)
    bb2 = Blackboard(bus2)
    try:
        assert bb2.read("strategy") is None
        bb2.rehydrate()
        got = bb2.read("strategy")
        assert got is not None and got.content_ref == "ch1/strategy-v2.md" and got.version == 2
    finally:
        bus2.close()


def test_studio_ghi_song_song_khong_mat_ban_ghi():
    """Bản trước K3.6b không khoá: hai lượt cùng đọc v0, cùng ghi v1, bản sau bị bỏ — mất hẳn artifact.

    Cửa sổ tranh chấp mở bằng Barrier trong `scope_of` (xem ca cùng tên ở `xagents-core`), không bằng `sleep`."""
    bus = InMemoryBus()
    bb = Blackboard(bus)
    real = bb.scope_of
    inside = threading.Barrier(2, timeout=2)

    def _scope(ns, pid):
        try: inside.wait()
        except threading.BrokenBarrierError: pass
        return real(ns, pid)
    bb.scope_of = _scope   # type: ignore[method-assign]

    loi: list[Exception] = []

    def ghi(i):
        try: bb.write("channel-strategist", "strategy", f"{i}.md")
        except Exception as e: loi.append(e)
    ts = [threading.Thread(target=ghi, args=(i,)) for i in range(2)]
    for t in ts: t.start()
    for t in ts: t.join()

    assert loi == []
    versions = sorted(e.payload["version"] for e in bus.replay(topic="shared-context"))
    assert versions == [1, 2], f"hai bản ghi phải ra hai version khác nhau, có {versions}"


def test_studio_co_content_toan_van_va_all_overview():
    bb = Blackboard(InMemoryBus())
    bb.write("channel-strategist", "strategy", "s.md", summary="tóm tắt", content="toàn văn chiến lược")
    assert bb.content("strategy") == "toàn văn chiến lược"
    assert bb.all()["strategy"].summary == "tóm tắt" and bb.overview() == {"strategy": "s.md"}


def test_studio_van_dung_lop_SharedContext_cua_minh():
    """`namespace` của studio là Literal riêng: dựng bằng lớp core là mất kiểm tra namespace."""
    from studio.events import SharedContext

    bb = Blackboard(InMemoryBus())
    assert type(bb.write("channel-strategist", "strategy", "s.md")) is SharedContext
    assert bb.snapshot() == {"strategy": bb.read("strategy")}, "snapshot() không tham số vẫn như trước K3.6b"
