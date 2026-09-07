"""Shim `studio.X -> xagents_core.X` (K3 kịch bản B) phải giữ ĐÚNG bề mặt cũ.

Bản studio của `software-company/tests/test_shim_core.py`. Studio nhận shim đầu tiên ở K3.2 (`studio.sandbox`, và
`studio.tools` re-export khung), nên file này sinh ra cùng lúc — thêm module chuyển sang core ở bước sau thì thêm
một dòng vào danh sách, đừng viết file mới.

Vì sao studio cần chốt riêng chứ không dựa vào chốt của company: hai bên nhập những tên KHÁC nhau từ cùng một
module core (studio dùng `clean_env` trực tiếp trong `media.py`/`qc.py`, company thì qua `workspace`), nên một
tên bị rơi khỏi shim này không có gì ở phía company đỏ lên.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import studio

# (tên module studio, tên module core). `studio.tools` không có ở đây: nó không phải shim — nó là bảng tool THẬT
# (`WebTools`) có re-export thêm phần khung, nên bề mặt của nó rộng hơn `__all__` của core.
SHIM: list[tuple[str, str]] = [("studio.sandbox", "xagents_core.sandbox")]


@pytest.mark.parametrize(("cong_ty", "core"), SHIM)
def test_shim_giu_du_ten_public(cong_ty: str, core: str):
    a, b = importlib.import_module(cong_ty), importlib.import_module(core)
    assert b.__all__, f"{core} phải khai `__all__` — nó là HỢP ĐỒNG của shim"
    for ten in b.__all__:
        assert hasattr(a, ten), f"{cong_ty} thiếu `{ten}` — shim không mang sang, người gọi vỡ ở chỗ khác"
        assert getattr(a, ten) is getattr(b, ten), f"{cong_ty}.{ten} không phải CÙNG đối tượng với {core}.{ten}"


@pytest.mark.parametrize(("cong_ty", "core"), SHIM)
def test_shim_khong_moc_lai_khung(cong_ty: str, core: str):
    """`studio/sandbox.py` TỪNG là bản sao 216 dòng của company mang nhãn "bản tạm, xoá ở K3.2". Ca này canh nó
    không mọc lại: được phép có hàm đọc cấu hình (`sandbox_from_config` — biến `STUDIO_SANDBOX*`, mặc định
    `subprocess`), không được phép định nghĩa lại kiểu hay backend."""
    src = (Path(studio.__file__).parent / f"{cong_ty.split('.')[-1]}.py").read_text(encoding="utf-8")
    cam = [ln for ln in src.splitlines() if ln.startswith(("class ", "@dataclass"))]
    assert not cam, f"{cong_ty} định nghĩa lại {cam} — khung phải ở core"


def test_tools_cua_studio_dung_chung_khung_voi_core():
    """Không phải shim, nhưng cùng một bất biến: `ToolBox` mà `WebTools` dựng phải LÀ lớp của core, không phải một
    lớp cùng tên. Hai lớp cùng tên khác định danh là cách bản fork quay lại mà mọi test vẫn xanh."""
    from xagents_core.tools import ToolBox, ToolCall, ToolError, ToolSpec

    from studio import tools
    for x in (ToolBox, ToolCall, ToolError, ToolSpec):
        assert getattr(tools, x.__name__) is x


def test_bang_tool_cua_studio_khong_cat_o_tang_bang():
    """Hành vi studio trước K3.2: `ToolBox` không cắt đầu ra, `web_fetch` tự cắt ở `MAX_CHARS`. Khung core mặc
    định cắt 6.000 (theo company), nên `toolbox()` phải khai `max_output=None` — quên là mất 14.000 ký tự cuối
    mỗi trang mà không có lỗi nào nổi lên."""
    from studio.tools import WebTools

    assert WebTools().toolbox().max_output is None


# ---------- K3.3b: `LLMConfig` của studio là ĐÚNG khung core, không thêm trường nào ----------

def test_llmconfig_khong_them_truong_rieng_nao():
    """Cả 13 trường studio từng khai đều là khoá chung, nên chúng đã lên core nguyên vẹn. Ca này là bánh cóc:
    thêm một trường ở studio thì phải trả lời được "company có sẵn thứ này chưa?" trước — nếu có, việc cần làm là
    kéo nó lên core, không phải mọc bản thứ hai."""
    from dataclasses import fields

    from xagents_core.llm import LLMConfig as CoreLLMConfig

    from studio.llm import LLMConfig

    assert issubclass(LLMConfig, CoreLLMConfig)
    assert {f.name for f in fields(LLMConfig)} == {f.name for f in fields(CoreLLMConfig)}


def test_thong_diep_thieu_model_goi_dung_bien_cua_studio():
    import pytest

    from studio.llm import LLMConfig, LLMError

    with pytest.raises(LLMError, match=r"STUDIO_MODEL_LIGHT hoặc llm\.yaml"):
        LLMConfig().model_for("light")


def test_core_config_cua_studio_dung_mot_nguon():
    from studio import llm
    from studio.core import CORE

    assert CORE.prefix == "STUDIO" and CORE.db_name == "studio.sqlite"
    assert (CORE.root / "agents").is_dir()
    assert llm.ROOT is CORE.root and llm.CONFIG_FILE == CORE.config_file
