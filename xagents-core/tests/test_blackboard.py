"""`xagents_core.blackboard` — tri thức chung theo namespace (K3.6b).

Dùng công ty GIẢ của `conftest.py`: blackboard ghi qua bus, nên ca ở đây cũng đi qua ACL và JSON Schema thật
như production. `giong` là namespace của `bien-tap`; `chung` là namespace toàn công ty (`global_namespaces`).
"""
from __future__ import annotations

import threading
from typing import ClassVar

import pytest

from conftest import FakeEnvelope, _schema
from xagents_core.blackboard import Blackboard
from xagents_core.bus import InMemoryBus, PermissionDenied
from xagents_core.config import CoreConfig, TopicACL
from xagents_core.events import SharedContext


class FakeContext(SharedContext):
    """Lớp con kiểu company: một trường core không được biết."""
    ghi_chu: str = ""


class Bus(InMemoryBus[FakeEnvelope]):
    envelope_cls = FakeEnvelope


class BB(Blackboard[FakeEnvelope, FakeContext]):
    envelope_cls = FakeEnvelope
    context_cls = FakeContext
    EXT: ClassVar[dict[str, str]] = {"giong": "yaml"}


@pytest.fixture
def cfg2(tmp_path):
    """Như `cfg` nhưng `shared-context` có hai chủ và một namespace toàn công ty."""
    import json
    d = tmp_path / "topics" / "schemas"; d.mkdir(parents=True)
    (d / "shared-context.json").write_text(json.dumps(_schema("shared-context", {
        "type": "object", "required": ["namespace", "version", "content_ref"], "additionalProperties": True,
        "properties": {"namespace": {"type": "string"}, "version": {"type": "integer"},
                       "content_ref": {"type": "string"}}})), encoding="utf-8")
    (d / "audit-log.json").write_text(json.dumps(_schema("audit-log", {
        "type": "object", "required": ["actor", "action"], "properties": {"actor": {"type": "string"},
                                                                          "action": {"type": "string"},
                                                                          "evidence": {"type": "string"}}})),
        encoding="utf-8")
    return CoreConfig(prefix="FAKE", root=tmp_path, db_name="fake.sqlite",
                      topic_acl=TopicACL(open_topics=frozenset({"audit-log", "shared-context"})),
                      namespace_owners={"giong": {"bien-tap"}, "chung": {"bien-tap"}},
                      global_namespaces=frozenset({"chung"}))


@pytest.fixture
def bb(cfg2):
    return BB(cfg2, Bus(cfg2))


# ---------- phạm vi ----------

def test_namespace_toan_cong_ty_khong_mang_tien_to_du_an(bb):
    assert bb.scope_of("chung", "DA1") == (None, "chung") and bb.context_key("chung", "DA1") == "chung"
    assert bb.scope_of("giong", "DA1") == ("DA1", "giong") and bb.context_key("giong", "DA1") == "DA1/giong"
    assert bb.context_key("giong", None) == "giong"


def test_hai_du_an_khong_ghi_de_nhau_con_namespace_chung_thi_dung_chung(bb):
    """ADR-0018. Không phân vùng thì agent của dự án B đọc phải artifact của dự án A."""
    bb.write("bien-tap", "giong", "A/g.md", project_id="DA1")
    bb.write("bien-tap", "giong", "B/g.md", project_id="DA2")
    assert bb.read("giong", "DA1").content_ref == "A/g.md"
    assert bb.read("giong", "DA2").content_ref == "B/g.md"
    assert bb.read("giong", "DA1").version == 1 and bb.read("giong", "DA2").version == 1

    bb.write("bien-tap", "chung", "bai-hoc.md", project_id="DA1")
    assert bb.read("chung", "DA2") is not None, "namespace toàn công ty dùng chung mọi dự án"
    assert set(bb.snapshot("DA1")) == {"giong", "chung"} and set(bb.snapshot("DA2")) == {"giong", "chung"}
    assert bb.snapshot("DA1")["giong"].content_ref == "A/g.md"
    assert bb.all()["DA1/giong"].content_ref == "A/g.md" and "chung" in bb.all()
    assert bb.overview() == {"chung": "bai-hoc.md", "DA1/giong": "A/g.md", "DA2/giong": "B/g.md"}


def test_version_tang_dan_va_ban_cu_khong_de_len_ban_moi(bb):
    bb.write("bien-tap", "giong", "v1.md")
    assert bb.write("bien-tap", "giong", "v2.md").version == 2
    assert bb.read("giong").version == 2 and bb.read("giong").content_ref == "v2.md"
    # event đến muộn với version nhỏ hơn: bỏ qua
    bb.bus.publish(FakeEnvelope(topic="shared-context", key="giong", actor="bien-tap",
                                payload=FakeContext(namespace="giong", version=1, content_ref="cu.md").model_dump()))
    assert bb.read("giong").content_ref == "v2.md"


def test_read_va_content_tra_none_khi_chua_co(bb):
    assert bb.read("giong") is None and bb.content("giong") is None
    bb.write("bien-tap", "giong", "g.md")
    assert bb.content("giong") is None, "chỉ có con trỏ thì content vẫn None"
    bb.write("bien-tap", "giong", "g.md", content="toàn văn")
    assert bb.content("giong") == "toàn văn"


# ---------- lớp con ----------

def test_dung_dung_lop_con_cua_cong_ty(bb):
    """`rulings` của company sống ở đây: dựng bằng `SharedContext` core là làm rơi nó im lặng."""
    bb.write("bien-tap", "giong", "g.md")
    got = bb.read("giong")
    assert type(got) is FakeContext and got.ghi_chu == ""


def test_ghi_di_qua_bus_nen_bi_kiem_quyen_chu_namespace(bb):
    with pytest.raises(PermissionDenied, match="namespace giong"):
        bb.write("nguoi-la", "giong", "g.md")


# ---------- mirror ra file ----------

def test_mirror_ghi_ban_theo_version_va_latest_dung_duoi_file(cfg2, tmp_path):
    store = tmp_path / "store"
    bb = BB(cfg2, Bus(cfg2), store=store)
    bb.write("bien-tap", "giong", "g.md", content="v1")
    bb.write("bien-tap", "giong", "g.md", content="v2", project_id="DA1")

    assert (store / "giong" / "v1.yaml").read_text(encoding="utf-8") == "v1"     # EXT của namespace
    assert (store / "giong" / "latest.yaml").read_text(encoding="utf-8") == "v1"
    assert (store / "DA1" / "giong" / "v1.yaml").exists(), "dự án khác nằm dưới tầng thư mục riêng"
    assert bb.path("chung") == store / "chung" / "latest.md", "namespace không có trong EXT thì là markdown"


def test_khong_co_store_thi_khong_mirror_va_path_tra_none(bb):
    bb.write("bien-tap", "giong", "g.md", content="x")
    assert bb.path("giong") is None


# ---------- rehydrate ----------

def test_rehydrate_dung_lai_tu_bus(cfg2):
    """Mở lại SQLite: blackboard rỗng, mọi thứ dựng lại từ log — không có bước này thì trạng thái lúc chạy
    khác trạng thái sau khi khởi động lại."""
    bus = Bus(cfg2)
    cu = BB(cfg2, bus)
    cu.write("bien-tap", "giong", "v1.md"); cu.write("bien-tap", "giong", "v2.md")

    moi = BB(cfg2, bus)
    assert moi.read("giong") is None
    moi.rehydrate()
    assert moi.read("giong").content_ref == "v2.md" and moi.read("giong").version == 2


def test_write_song_song_khong_mat_ban_ghi(cfg2):
    """Đánh version là đọc-sửa-ghi. Không khoá thì hai thread cùng đọc ra v0 và cùng ghi v1; bản sau bị `_on`
    bỏ vì không lớn hơn — mất hẳn một artifact. Bản studio trước K3.6b không có khoá này.

    Cửa sổ tranh chấp mở bằng **Barrier trong `scope_of`** (chạy ngay trước lúc đọc version), không bằng
    `sleep`: có khoá thì luồng thứ hai còn chưa vào tới đó nên barrier vỡ theo timeout và hai bản ra 1, 2;
    bỏ khoá thì cả hai gặp nhau và cùng ra 1. Đây là điểm khác giữa một ca ĐO được cái khoá và một ca chỉ
    tình cờ xanh vì GIL — spawn thread rồi mong có va chạm thì không đo gì cả."""
    bb = BB(cfg2, Bus(cfg2))
    real = bb.scope_of
    inside = threading.Barrier(2, timeout=2)

    def _scope(ns, pid):
        try: inside.wait()
        except threading.BrokenBarrierError: pass
        return real(ns, pid)
    bb.scope_of = _scope   # type: ignore[method-assign]

    loi: list[Exception] = []

    def ghi(i):
        try: bb.write("bien-tap", "giong", f"{i}.md")
        except Exception as e: loi.append(e)
    ts = [threading.Thread(target=ghi, args=(i,)) for i in range(2)]
    for t in ts: t.start()
    for t in ts: t.join()

    assert loi == []
    versions = sorted(e.payload["version"] for e in bb.bus.replay(topic="shared-context"))
    assert versions == [1, 2], f"hai bản ghi phải ra hai version khác nhau, có {versions}"
    assert bb.read("giong").version == 2
