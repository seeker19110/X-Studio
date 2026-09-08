"""Khuôn lỗi lặp lại của `TRAPS.md` §1, canh trên mã của studio.

Đây không phải test tính năng — là test QUY ƯỚC, đối xứng với `software-company/tests/test_orch_khuon_loi.py`.
Nó tồn tại vì bản guard của company khoá phạm vi ở `src/company/orch/`, nên studio nằm ngoài tầm với: khuôn 3
(`once=f"gate:{sid}"`, `gate.overdue` bị lần nhắc nuốt) sống trong `studio/orchestrator.py` suốt thời gian
company đã vá xong. Đỏ ở đây nghĩa là một PR sau đã phá quy ước, không phải một tính năng hỏng.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "studio"
SRC = {p.name: p.read_text(encoding="utf-8") for p in SRC_DIR.glob("*.py")}

# Khoá `once` trong studio được viết theo đúng hai lối, và guard bắt cả hai:
#   - inline:      `_remember(f"…")`, `once=f"…"`, `once_key=f"…"`
#   - qua biến:    `key = f"…"` rồi `self.once` / `_remember(key)`
# Lối thứ hai từng là lỗ hổng của bản guard bên company (nó chỉ bắt inline). Kiểm lại giả định "biến luôn tên
# `key`" bằng `test_moi_khoa_once_deu_di_qua_mot_trong_hai_loi` bên dưới — đừng kế thừa nó mà không đo.
PATTERN = re.compile(r'(?:_remember|once(?:_key)?=)\(?f"([^"]+)"|(?:^|\s)key = f"([^"]+)"', re.MULTILINE)

# Miễn vì lý do RÕ, không phải vì "chưa ai kêu ca". Rỗng: hiện KHÔNG khoá nào của studio cần miễn.
KHOA_MIEN: frozenset[str] = frozenset()


def _khoa(src: str) -> list[str]:
    return [a or b for a, b in PATTERN.findall(src)]


def _co_the_he(key_tmpl: str) -> bool:
    """Khoá mang thế hệ khi có ≥ 2 thành phần, HOẶC khi thành phần duy nhất là `event_id` — `event_id` đổi mỗi
    lần một event mới tới, nên một lượt "hợp lệ lặp lại" tự nhiên có khoá khác và không đụng khoá cũ."""
    phan = re.findall(r"\{([^}]+)\}", key_tmpl)
    return len(phan) >= 2 or any(x.strip().endswith("event_id") for x in phan)


def test_khuon3_khoa_once_mang_the_he_hoac_nam_trong_danh_sach_mien():
    """Mọi khoá `once` phải phân biệt được hai lần XẢY RA HỢP LỆ của cùng một chủ thể, không chỉ danh tính nó.
    `gate:{sid}` (một thành phần, dùng chung cho `remind` 12h và `overdue` 24h) là bản gốc của bài học này."""
    vi_pham: list[str] = []
    for name, src in SRC.items():
        for key_tmpl in _khoa(src):
            prefix = key_tmpl.split(":", 1)[0].split("{", 1)[0]
            if _co_the_he(key_tmpl) or prefix in KHOA_MIEN: continue
            vi_pham.append(f"{name}: {key_tmpl!r} (không mang thế hệ, không nằm trong KHOA_MIEN)")
    assert not vi_pham, "khoá không thế hệ, không miễn — lần lặp lại HỢP LỆ sẽ bị once nuốt:\n" + "\n".join(vi_pham)


def test_guard_bat_duoc_mot_vi_pham_da_biet():
    """TRAPS §2, "tin test canh quy ước kiểu grep": một mẫu regex xanh chỉ chứng minh những gì nó NHÌN THẤY là
    sạch. Chạy mẫu trên đúng con bug lịch sử (cả hai lối viết) trước khi tin nó."""
    assert _khoa('self._audit("gate.overdue", d, once=f"gate:{sid}")') == ["gate:{sid}"]
    assert _khoa('        key = f"publish:{vid}"') == ["publish:{vid}"]
    assert not _co_the_he("gate:{sid}"), "mẫu phải kết luận `gate:{sid}` là KHÔNG có thế hệ"
    assert _co_the_he("gate:{sid}:{pha}:{the_he}") and _co_the_he("x:{event_id}")


def test_moi_khoa_once_deu_di_qua_mot_trong_hai_loi():
    """Giả định của guard: khoá `once` viết qua biến thì biến tên `key`. Nếu một PR sau đặt tên khác
    (`k = f"…"`, `once_key = f"…"`), guard trên sẽ xanh một cách vô nghĩa — test này bắt đúng lúc đó."""
    src = SRC["orchestrator.py"]
    ten_bien = set(re.findall(r"(\w+) = f\"[^\"]*\"", src))
    dung_lam_once = {t for t in ten_bien if re.search(rf"\b{t}\b (?:in|not in) self\.once|_remember\({t}\)", src)}
    assert dung_lam_once <= {"key"}, f"khoá once qua biến tên lạ, guard không nhìn thấy: {sorted(dung_lam_once)}"


def test_khuon3_gate_remind_va_overdue_khong_dung_chung_khoa():
    """Hồi quy hẹp cho chính dòng đã hỏng: khoá của `gate.{pha}` phải chứa `{pha}`, nếu không lần nhắc (12h)
    ghi khoá trước và lần quá hạn (24h) không bao giờ vào audit-log. Kịch bản chạy thật ở
    `test_orchestrator_extra.py::test_tick_gate_overdue_khong_bi_lan_remind_nuot`."""
    src = SRC["orchestrator.py"]
    m = re.search(r'self\._audit\(f"gate\.\{pha\}".*?once=f"([^"]+)"', src, re.S)
    assert m, "không còn dòng audit gate.{pha} — nếu đổi cấu trúc, sửa cả test này cho khớp"
    assert "{pha}" in m.group(1), f"khoá {m.group(1)!r} không mang giai đoạn: remind sẽ nuốt overdue"
