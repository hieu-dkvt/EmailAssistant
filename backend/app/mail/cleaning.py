"""Làm sạch nội dung mail: cắt quote và chữ ký (spec 2026-10-17-content-cleaning-design.md).

Hàm thuần, không I/O. Xử lý theo dòng; vị trí cắt tính theo ký tự của body_text gốc.
Đổi quy tắc thì tăng CLEANER_VERSION: worker làm sạch lại mọi mail có version khác.
"""

import re
from dataclasses import dataclass
from functools import cached_property
from typing import Literal

from app.mail.subject import normalize_subject

CLEANER_VERSION = 1

MAX_SIGNATURE_LINES = 15
RULE_CONTACT_WINDOW = 6
HEADER_BLOCK_WINDOW = 6
CONTACT_BLOCK_MAX_LINES = 8
CONTACT_BLOCK_MAX_WIDTH = 80
# Dòng dài hơn mức này không bao giờ là mốc hay chữ ký: tránh regex chạy lâu trên dòng rất dài
# (HTML chuyển sang text có thể thành một dòng hàng trăm nghìn ký tự).
MAX_MARKER_LINE = 1000

type CutKind = Literal["quote", "signature"]


@dataclass(frozen=True)
class Cut:
    kind: CutKind
    start: int
    end: int


@dataclass(frozen=True)
class CleanResult:
    body_clean: str
    cuts: list[Cut]


@dataclass(frozen=True, eq=False)
class _Line:
    start: int  # vị trí ký tự đầu dòng trong body
    text: str  # nội dung, không có "\n" và "\r" cuối

    @property
    def blank(self) -> bool:
        return not self.text.strip()

    @property
    def quote(self) -> bool:
        return self.text.lstrip().startswith(">")

    @property
    def short(self) -> bool:
        return len(self.text) <= MAX_MARKER_LINE

    # Mỗi dòng nằm trong nhiều cửa sổ tìm chữ ký: tính một lần.
    @cached_property
    def contact(self) -> bool:
        return _has_contact(self.text)

    @cached_property
    def company(self) -> bool:
        return _has_company(self.text)


# Lookbehind: chỉ thử ở đầu một cụm ký tự địa chỉ. Không có nó, dòng dài không có '@' làm search
# chạy bậc hai (review bảo mật 2026-10-17: dòng 20.000 ký tự mất 2 giây).
_ADDR = r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_EMAIL = re.compile(_ADDR)
_DATE_TIME = re.compile(
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{1,2}:\d{2}"
)
_WROTE_END = re.compile(r"(?:\bwrote|đã viết)\s*[:：]\s*$|書きました\s*[:：]?\s*$", re.IGNORECASE)
_COLON_END = re.compile(r"[:：]\s*$")
_SENTENCE_END = re.compile(r"[。.!?！？]\s*$")

_ORIGINAL_SEP = re.compile(
    r"^\s*-{2,}\s*(?:original message|元のメッセージ)\s*-{2,}\s*$", re.IGNORECASE
)
_FORWARD_SEP = re.compile(
    r"^\s*-{2,}\s*(?:forwarded message|転送メッセージ|転送されたメッセージ)\s*-{2,}\s*$"
    r"|^\s*begin forwarded message\s*:\s*$",
    re.IGNORECASE,
)
_UNDERSCORE_RULE = re.compile(r"^\s*_{20,}\s*$")


def _header_field(names: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*\*?\s*(?:{names})\s*\*?\s*[:：]", re.IGNORECASE)


_HDR_FROM = _header_field("from|差出人|送信者|từ")
_HDR_DATE = _header_field("sent|date|送信日時|日付|gửi|ngày")
_HDR_SUBJECT = _header_field("subject|件名|chủ đề|tiêu đề")

_RULE = re.compile(r"^[━─═=＝\-_＿*＊~～・◆◇■□●○☆★]{6,}$")
_CONTACT = re.compile(
    r"(?:\b(?:tel|fax|phone|mobile|e-?mail|mail|url|skype|hotline)\b|携帯|電話|điện thoại|\bđt\b)"
    r"\s*[.:：]"
    r"|〒|https?://|" + _ADDR,
    re.IGNORECASE,
)
_COMPANY = re.compile(
    r"株式会社|有限会社|合同会社|㈱|\(株\)|\bco\.,?\s*ltd\b|\binc\.|\bcorporation\b|\bcorp\.|"
    r"công ty|\bjsc\b",
    re.IGNORECASE,
)
_MOBILE_FOOTER = re.compile(
    r"^\s*(?:sent from my\b.*|get outlook for\b.*|\S.{0,30}から送信\s*|gửi từ .* của tôi\s*)$",
    re.IGNORECASE,
)
# Lọc nhanh bằng chuỗi con (điều kiện cần của hai regex trên) trước khi chạy regex nhiều nhánh,
# không phân biệt hoa thường: regex mất khoảng 0,4 ms trên một dòng 1.000 ký tự.
_CONTACT_CHARS = ":：.〒@"
_COMPANY_HINTS = (
    "株式会社",
    "有限会社",
    "合同会社",
    "㈱",
    "(株)",
    "co.",
    "inc.",
    "corp",
    "công ty",
    "jsc",
)


def _has_contact(text: str) -> bool:
    return any(c in text for c in _CONTACT_CHARS) and _CONTACT.search(text) is not None


def _has_company(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in _COMPANY_HINTS) and _COMPANY.search(text) is not None


def clean_body(body: str, subject: str | None = None) -> CleanResult:
    lines = _split_lines(body)
    n = len(lines)
    quotes: list[tuple[int, int]] = []  # khoảng dòng [i, j) bị cắt là quote
    own_end = n  # dòng từ own_end trở đi: đã cắt tới hết, hoặc nội dung chuyển tiếp (giữ)

    marker = _first_marker(lines)
    if marker is not None:
        kind, start, stop = marker
        forward = kind == "forward" or (
            kind == "outlook" and normalize_subject(subject).outer == "forward"
        )
        if forward:
            own_end = start
        elif kind == "outlook":
            quotes.append((start, n))
            own_end = start
        else:
            rest = lines[stop:]
            inline = any(ln.quote for ln in rest) and any(
                not ln.quote and not ln.blank for ln in rest
            )
            if inline:
                quotes.append((start, stop))
            else:
                quotes.append((start, n))
                own_end = start

    i = 0
    while i < own_end:
        if lines[i].quote and not _covered(i, quotes):
            j = i
            while j < own_end and lines[j].quote:
                j += 1
            quotes.append((i, j))
            i = j
        else:
            i += 1

    quotes = _merge(sorted(quotes))
    ranges: list[tuple[CutKind, int, int]] = [("quote", a, b) for a, b in quotes]
    signature = _find_signature(lines, _segments(own_end, quotes))
    if signature is not None:
        ranges.append(("signature", *signature))
    ranges.sort(key=lambda r: r[1])

    cuts = [Cut(kind, lines[a].start, _offset(lines, b, len(body))) for kind, a, b in ranges]
    return CleanResult(apply_cuts(body, cuts), cuts)


def apply_cuts(body: str, cuts: list[Cut]) -> str:
    """body bỏ các đoạn cắt rồi chuẩn hóa: bỏ khoảng trắng cuối dòng, nhiều dòng trống liên tiếp
    còn một, bỏ dòng trống ở đầu và cuối."""
    parts: list[str] = []
    last = 0
    for c in cuts:
        parts.append(body[last : c.start])
        last = c.end
    parts.append(body[last:])
    out: list[str] = []
    for line in "".join(parts).split("\n"):
        line = line.rstrip()
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _split_lines(body: str) -> list[_Line]:
    lines: list[_Line] = []
    pos = 0
    for raw in body.split("\n"):
        lines.append(_Line(pos, raw.removesuffix("\r")))
        pos += len(raw) + 1
    return lines


def _offset(lines: list[_Line], index: int, length: int) -> int:
    return lines[index].start if index < len(lines) else length


def _covered(i: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= i < b for a, b in ranges)


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for a, b in ranges:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _segments(own_end: int, quotes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Các đoạn tự viết: khoảng dòng liên tục trước own_end, không bị cắt quote."""
    segments: list[tuple[int, int]] = []
    start = 0
    for a, b in quotes:
        if a >= own_end:
            break
        if a > start:
            segments.append((start, a))
        start = max(start, b)
    if start < own_end:
        segments.append((start, own_end))
    return segments


# --- mốc lịch sử (spec mục 4.1) ---


def _first_marker(lines: list[_Line]) -> tuple[str, int, int] | None:
    """(loại, dòng đầu, dòng sau mốc) của mốc đầu tiên. Loại: forward, outlook, attribution."""
    for i, line in enumerate(lines):
        if line.quote or line.blank or not line.short:
            continue
        if _FORWARD_SEP.match(line.text):
            return "forward", i, i + 1
        if _ORIGINAL_SEP.match(line.text):
            return "outlook", i, i + 1
        if _is_header_block(lines, i):
            return "outlook", _header_block_start(lines, i), i + 1
        if _is_attribution(line.text):
            return "attribution", i, i + 1
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is not None and nxt.short and not nxt.quote and not nxt.blank:
            joined = line.text.rstrip() + " " + nxt.text.strip()
            # Gmail gấp dòng giới thiệu dài. Dòng dưới tự nó đã là dòng giới thiệu thì chỉ ghép
            # khi dòng trên có ngày/giờ và không phải câu đã kết thúc (tránh nuốt nội dung).
            if _is_attribution(joined) and (
                not _is_attribution(nxt.text)
                or (_DATE_TIME.search(line.text) and not _SENTENCE_END.search(line.text))
            ):
                return "attribution", i, i + 2
    return None


def _is_attribution(text: str) -> bool:
    t = text.strip()
    # Loại nhanh trước khi chạy regex: dòng giới thiệu luôn kết thúc bằng ':' / '：' / '書きました'.
    if not t or (t[-1] not in ":：" and not t.endswith("書きました")):
        return False
    if _WROTE_END.search(t):
        return bool(_EMAIL.search(t) or _DATE_TIME.search(t))
    if _COLON_END.search(t):
        return bool(_EMAIL.search(t) and _DATE_TIME.search(t))
    return False


def _is_header_block(lines: list[_Line], i: int) -> bool:
    if not _HDR_FROM.match(lines[i].text):
        return False
    window = [ln.text for ln in lines[i + 1 : i + 1 + HEADER_BLOCK_WINDOW] if ln.short]
    return any(_HDR_DATE.match(t) for t in window) and any(_HDR_SUBJECT.match(t) for t in window)


def _header_block_start(lines: list[_Line], i: int) -> int:
    j = i - 1
    while j >= 0 and lines[j].blank:
        j -= 1
    if (
        j >= 0
        and lines[j].short
        and (_UNDERSCORE_RULE.match(lines[j].text) or _ORIGINAL_SEP.match(lines[j].text))
    ):
        return j
    return i


# --- chữ ký (spec mục 4.4) ---


def _find_signature(lines: list[_Line], segments: list[tuple[int, int]]) -> tuple[int, int] | None:
    seen_content = False
    for start, end in segments:
        for c in range(start, end):
            line = lines[c]
            if line.blank:
                continue
            if (
                seen_content
                and line.short
                and _is_signature_start(lines, c, end)
                and len(_nonblank(lines, c, end, MAX_SIGNATURE_LINES)) <= MAX_SIGNATURE_LINES
            ):
                return c, end
            seen_content = True
    return None


def _is_signature_start(lines: list[_Line], c: int, end: int) -> bool:
    line = lines[c]
    text = line.text.strip()
    if text == "--":
        return True
    if _RULE.match(text):
        window = lines[c + 1 : min(end, c + 1 + RULE_CONTACT_WINDOW)]
        return any(ln.short and (ln.contact or ln.company) for ln in window)
    if _MOBILE_FOOTER.match(text):
        return True
    if line.company:
        block = _nonblank(lines, c, end, CONTACT_BLOCK_MAX_LINES)
        return (
            len(block) <= CONTACT_BLOCK_MAX_LINES
            and all(len(ln.text) <= CONTACT_BLOCK_MAX_WIDTH for ln in block)
            and any(ln.contact for ln in block[1:])
        )
    return False


def _nonblank(lines: list[_Line], start: int, end: int, limit: int) -> list[_Line]:
    """Các dòng không trống trong [start, end), dừng khi đã quá `limit` dòng (tránh bậc hai)."""
    out: list[_Line] = []
    for i in range(start, end):
        if not lines[i].blank:
            out.append(lines[i])
            if len(out) > limit:
                break
    return out
