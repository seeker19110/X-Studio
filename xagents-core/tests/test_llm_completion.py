"""`Completion` + đường bóc JSON chung (K3.3c1).

Đây là chỗ hai công ty **mâu thuẫn thật**, không phải chỉ trôi khỏi nhau: company bóc fence bao ngoài rồi đòi
JSON đứng ngay từ ký tự đầu (hỏng giữa cấu trúc thì ĐỎ, cố ý); studio đi TÌM object trong văn xuôi (cứu được
lượt model kể chuyện trước rồi mới trả JSON). Bản hợp nhất giữ cả hai bằng một điều kiện: **có văn xuôi đứng
trước thì đi tìm; đầu ra tự nhận là JSON ngay từ đầu mà hỏng thì vẫn đỏ.**

Mỗi ca dưới đây là một dòng trong bảng quyết định đó. Ca quan trọng nhất là
`test_json_hong_giua_cau_truc_van_do` — nó canh nửa mà bản permissive của studio sẽ nuốt mất.
"""
from __future__ import annotations

import pytest

from xagents_core.llm import (
    Completion,
    LLMError,
    object_before_trailing_junk,
    object_in_prose,
    strip_code_fence,
)


def _c(text: str) -> Completion:
    return Completion(text=text, input_tokens=0, output_tokens=0, model="m")


# ---------- bóc fence bao ngoài ----------

@pytest.mark.parametrize("text", [
    '{"a": 1}',                                   # không fence
    '```{"a": 1}```',                             # fence một dòng, không xuống dòng
    '```json\n{"a": 1}\n```',                     # fence có ngôn ngữ
    '```\n{"a": 1}\n```   \n\n',                  # khoảng trắng thừa sau fence đóng
    '```json {"a": 1}```',                        # fence một dòng có ngôn ngữ
    '```json\n{"a": 1}',                          # thiếu fence đóng
])
def test_strip_fence_giu_moi_dang_boc(text: str):
    assert _c(text).json() == {"a": 1}


def test_strip_fence_mot_dong_khong_tach_duoc_thi_tra_nguyen():
    """`​```json` trần: không có gì sau nhãn để tách → trả nguyên phần còn lại, không được ném IndexError."""
    assert strip_code_fence("```json") == "json"


def test_fence_ben_trong_chuoi_khong_bi_cat():
    """Fence nằm trong giá trị chuỗi là nội dung thật, không phải fence đóng — cắt theo nó là chặt cụt JSON."""
    inner = "cấu hình:\\n```yaml\\nkey: value\\n```\\nhết"
    assert _c('```json\n{"note": "' + inner + '", "cuoi": true}\n```').json()["cuoi"] is True


# ---------- thừa dấu đóng ở cuối (bản company) ----------

def test_thua_dau_dong_o_cuoi_van_lay_duoc_object():
    assert _c('{"a": 1}}').json() == {"a": 1}
    assert object_before_trailing_junk('{"a": 1}\n]  }') == {"a": 1}


def test_thua_thu_khac_dau_dong_thi_khong_cuu():
    assert object_before_trailing_junk('{"a": 1} thêm chữ') is None


def test_khong_phai_object_thi_khong_cuu():
    assert object_before_trailing_junk('[1, 2]]') is None, "mảng không phải payload của topic nào"
    assert object_before_trailing_junk("khong phai json") is None


def test_khong_thua_gi_thi_de_duong_thuong_lo():
    assert object_before_trailing_junk('{"a": 1}') is None, "không có phần dư thì `json.loads` đã lo xong"


# ---------- object trong văn xuôi (bản studio) ----------

def test_van_xuoi_roi_fence():
    assert _c('trước đó model kể lể...\n```json\n{"a": 1}\n```\nsau đó').json() == {"a": 1}


def test_fence_hong_thi_roi_xuong_quet_ngoac():
    assert _c('```json\nkhong phai json\n```\nvậy thì đây {"a": 1}').json() == {"a": 1}


def test_quet_ngoac_lui_dan_khi_co_dau_dong_lac():
    assert _c('kể chuyện {"a": 1} rồi thêm } linh tinh sau').json() == {"a": 1}


def test_van_xuoi_khong_co_object_nao():
    assert object_in_prose("không có JSON nào ở đây cả") is None
    assert object_in_prose('có mở { mà không có đóng') is None
    assert object_in_prose('mở { rồi đóng } nhưng rỗng ruột {,}') is None


# ---------- quyết định hợp nhất ----------

def test_json_hong_giua_cau_truc_van_do():
    """Model đóng object sớm rồi viết tiếp — company cố ý ĐỎ, và bản hợp nhất giữ nguyên điều đó.

    Phép quét của studio sẽ "cứu" ca này thành `{"a": 1}`, tức là ticket đi tiếp với một nửa dữ liệu mà không
    ai biết. Ranh giới là chỗ JSON bắt đầu: ở đây đầu ra tự nhận là JSON ngay từ ký tự đầu.
    """
    with pytest.raises(LLMError, match="không phải JSON"):
        _c('{"a": 1},{"b": 2}').json()


def test_van_xuoi_dung_truoc_thi_moi_di_tim():
    """Cùng một đầu ra hỏng như trên, nhưng có văn xuôi đứng trước → đi tìm, và tìm thấy."""
    assert _c('model kể: {"a": 1},{"b": 2}').json() == {"a": 1}


def test_loi_json_chi_trich_doan_quanh_vi_tri_loi():
    with pytest.raises(LLMError) as e:
        _c('{"a": 1, "b": ' + "x" * 2000).json()
    assert "gần vị trí lỗi" in str(e.value) and len(str(e.value)) < 600


# ---------- token ----------

def test_tokens_va_ty_le_cache():
    c = Completion(text="{}", input_tokens=1_000, output_tokens=300, model="m", cached_input_tokens=600)
    assert c.tokens == 1_300 and c.cache_hit_ratio == 0.6
    assert Completion(text="{}", input_tokens=0, output_tokens=0, model="m").cache_hit_ratio == 0.0


def test_truong_moi_cua_studio_co_mac_dinh():
    """`cache_write_tokens` và `tool_mode` là phần studio nhận thêm; mặc định phải giữ mọi `Completion(...)` cũ
    của studio dựng được y như trước."""
    c = Completion(text="{}", input_tokens=1, output_tokens=1, model="m")
    assert c.cache_write_tokens == 0 and c.tool_mode == "" and c.tool_calls == []
