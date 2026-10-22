"""Làm nổi bật từ khóa và đoạn trích (spec 14 mục 6).

Trả danh sách đoạn {text, hit} thay vì vị trí ký tự: vị trí trong Python (code point) và
JavaScript (UTF-16) lệch nhau khi có ký tự ngoài BMP. So khớp sau chuẩn hóa từng ký tự (NFKC +
lower) như normalizer của index, có bảng ánh xạ về vị trí gốc. Hàm thuần, không đụng DB.
"""

import re
import unicodedata
from collections.abc import Sequence

from app.web_schemas import Segment

HIGHLIGHT_LIMIT = 300_000
SNIPPET_BEFORE = 60
SNIPPET_AFTER = 140
SNIPPET_DEFAULT = 200
_SPACES = re.compile(r"\s+")


def _fold(text: str) -> tuple[str, list[int] | None]:
    """Chuỗi đã chuẩn hóa và vị trí gốc của từng ký tự (None: trùng vị trí gốc)."""
    lowered = text.lower()
    if len(lowered) == len(text) and (text.isascii() or unicodedata.is_normalized("NFKC", text)):
        return lowered, None
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        folded = ch.lower() if ch.isascii() else unicodedata.normalize("NFKC", ch).lower()
        out.append(folded)
        index.extend([i] * len(folded))
    return "".join(out), index


def _normalize_keyword(keyword: str) -> str:
    return unicodedata.normalize("NFKC", keyword).lower()


def find_spans(text: str, keywords: Sequence[str]) -> list[tuple[int, int]]:
    """Các đoạn [start, end) khớp từ khóa trong HIGHLIGHT_LIMIT ký tự đầu, đã gộp chồng nhau."""
    if not text or not keywords:
        return []
    head = text[:HIGHLIGHT_LIMIT]
    folded, index = _fold(head)
    spans: list[tuple[int, int]] = []
    for keyword in keywords:
        needle = _normalize_keyword(keyword)
        if not needle:
            continue
        pos = folded.find(needle)
        while pos != -1:
            end = pos + len(needle)
            if index is None:
                spans.append((pos, end))
            else:
                spans.append((index[pos], index[end - 1] + 1))
            pos = folded.find(needle, end)
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _segments(text: str, spans: Sequence[tuple[int, int]], offset: int = 0) -> list[Segment]:
    """Đoạn của text (text là phần [offset, offset+len) của văn bản gốc mà spans tham chiếu)."""
    out: list[Segment] = []
    pos = 0
    for start, end in spans:
        start, end = max(start - offset, 0), min(end - offset, len(text))
        if end <= pos or start >= len(text):
            continue
        start = max(start, pos)
        if start > pos:
            out.append(Segment(text=text[pos:start]))
        out.append(Segment(text=text[start:end], hit=True))
        pos = end
    if pos < len(text):
        out.append(Segment(text=text[pos:]))
    return out


def highlight(text: str | None, keywords: Sequence[str]) -> list[Segment]:
    if not text:
        return []
    return _segments(text, find_spans(text, keywords))


def snippet(text: str | None, keywords: Sequence[str]) -> list[Segment]:
    """Đoạn trích quanh lần khớp đầu tiên; không khớp thì đầu văn bản. Xuống dòng → khoảng trắng."""
    if not text:
        return []
    spans = find_spans(text, keywords)
    if spans:
        start = max(0, spans[0][0] - SNIPPET_BEFORE)
        end = min(len(text), spans[0][1] + SNIPPET_AFTER)
    else:
        start, end = 0, min(len(text), SNIPPET_DEFAULT)
    segments = _segments(text[start:end], spans, offset=start)
    for seg in segments:
        seg.text = _SPACES.sub(" ", seg.text)
    if segments and not segments[0].hit:
        segments[0].text = segments[0].text.lstrip()
    if start > 0:
        segments.insert(0, Segment(text="…"))
    if end < len(text):
        segments.append(Segment(text="…"))
    return [s for s in segments if s.text]
