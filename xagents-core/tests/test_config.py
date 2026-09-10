"""K3.0 — khung `xagents-core`. Package còn rỗng, nên test ở đây canh ba thứ mà bảy bước sau xây LÊN TRÊN:
`CoreConfig` bất biến, đường dẫn suy ra từ `root` (không từ `__file__` của core), và tên biến môi trường đi qua
một chỗ duy nhất.

Đây là test QUY ƯỚC, không phải test tính năng: đỏ ở đây nghĩa là một PR sau đã phá hợp đồng ADR gốc 0001.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import xagents_core
from xagents_core import CoreConfig, TopicACL


def _cfg(**them: object) -> CoreConfig:
    return CoreConfig(prefix="COMPANY", root=Path("/x/software-company"), db_name="company.sqlite", **them)  # type: ignore[arg-type]


def test_package_nhap_duoc_va_co_version():
    assert xagents_core.__version__
    assert set(xagents_core.__all__) == {"CoreConfig", "TopicACL", "__version__"}


def test_duong_dan_suy_tu_root_khong_tu_vi_tri_cua_core():
    """Core không được đoán đường dẫn từ `__file__` của chính nó — làm thế là core biết mình nằm ở đâu so với
    công ty, tức buộc chặt vào bố cục repo và công ty thứ ba phải nằm đúng chỗ mới chạy được."""
    cfg = _cfg()
    assert cfg.config_file == Path("/x/software-company/llm.yaml")
    assert cfg.schema_dir == Path("/x/software-company/topics/schemas")
    assert cfg.agents_dir == Path("/x/software-company/agents")
    assert cfg.skills_dir == Path("/x/software-company/skills")
    khac = CoreConfig(prefix="STUDIO", root=Path("/y/Studio-creators"), db_name="studio.sqlite")
    assert khac.config_file == Path("/y/Studio-creators/llm.yaml")


def test_ten_bien_moi_truong_di_qua_mot_cho():
    """Viết thẳng `f"{prefix}_..."` ở nơi dùng thì mỗi nơi tự chọn dấu nối, và một chỗ gõ sai đọc ra biến
    không tồn tại mà không ai biết — lỗi im lặng, đúng khuôn 1 của TRAPS."""
    cfg = _cfg()
    assert cfg.env_name("MODEL_STRONG") == "COMPANY_MODEL_STRONG"
    assert cfg.approvers_env == "COMPANY_GATE_APPROVERS"
    assert CoreConfig(prefix="STUDIO", root=Path("/y"), db_name="s.sqlite").approvers_env == "STUDIO_GATE_APPROVERS"


def test_cau_hinh_bat_bien():
    """`CoreConfig` dựng MỘT lần cho cả tiến trình rồi truyền xuống khắp nơi. Sửa được nó lúc chạy là một dạng
    state RAM không sống sót qua restart (khuôn 2 của TRAPS): hai tiến trình cùng mã sẽ chạy hai cấu hình khác
    nhau mà không ai biết."""
    cfg = _cfg()
    with pytest.raises(AttributeError):
        cfg.prefix = "STUDIO"   # type: ignore[misc]
    with pytest.raises(AttributeError):
        TopicACL().human_topics = frozenset({"x"})   # type: ignore[misc]


def test_mac_dinh_rong_chu_khong_phai_none():
    """Mọi tập/ánh xạ mặc định là rỗng, không phải `None`: nơi dùng lặp thẳng, không phải `or {}` ở từng chỗ —
    và một chỗ quên `or {}` là `TypeError` giữa phiên chạy thật."""
    cfg = _cfg()
    assert cfg.payload_models == {} and cfg.namespace_owners == {} and cfg.transitions == {}
    assert cfg.external_topics == frozenset() and cfg.derived_topics == frozenset()
    acl = cfg.topic_acl
    assert acl.producers == {} and acl.human_topics == frozenset() and acl.open_topics == frozenset()
    assert acl.engineering_actors == frozenset() and acl.review_producers == frozenset()


def test_moi_cau_hinh_co_topic_acl_rieng():
    """`field(default_factory=...)` chứ không phải một `TopicACL()` dùng chung: hai công ty chạy trong cùng một
    tiến trình (console nhập cả hai) không được chia nhau một đối tượng cấu hình."""
    assert _cfg().topic_acl is not _cfg().topic_acl


def test_py_typed_ton_tai_va_duoc_dong_goi():
    """PEP 561. Thiếu marker này thì mypy coi CẢ `xagents_core` là untyped: shim
    `from xagents_core.X import *` ở company/studio mang sang **0 tên**, caller nhận `has no attribute` —
    hoặc tệ hơn, `Any` im lặng ở chỗ có `--ignore-missing-imports`. Đo được thật ở K3.1: `company/runner.py`
    gọi `fit` bị mypy báo `Module "company.context" has no attribute "fit"` cho tới khi thêm file này.

    Nói cách khác: không có nó thì `strict = true` của core chỉ bảo vệ chính core, không bảo vệ ai gọi nó —
    mà cả bảy bước K3 đều là "chuyển module sang core rồi để người khác gọi qua shim"."""
    import tomllib

    marker = Path(xagents_core.__file__).parent / "py.typed"
    assert marker.exists(), "thiếu src/xagents_core/py.typed"
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    inc = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert "src/xagents_core/py.typed" in inc, "py.typed phải nằm trong wheel, không chỉ trên đĩa lúc dev"
