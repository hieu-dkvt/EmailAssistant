"""Chia ngân sách ngữ cảnh gửi AI cho hỏi đáp (spec 27 mục 6). Hàm thuần: không DB, không I/O.

Vị trí tính theo code point của thân mail (giống `start_pos`, `end_pos` của đoạn). Module này
không log và không giữ text mail ngoài tham số của `render_body`.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

type Span = tuple[int, int]
type ChunkKey = tuple[int, int, int]  # (message_id, start, end)

CONTEXT_MAX_CHARS = 24_000
SOURCE_MAX_CHARS = 3_000
PREVIOUS_MAX_CHARS = 1_200
MIN_EXPANSION_CHARS = 200

ELLIPSIS_SEPARATOR = "\n…\n"


@dataclass(frozen=True)
class SourceSpans:
    message_id: int
    spans: tuple[Span, ...]


@dataclass(frozen=True)
class PreviousMail:
    message_id: int
    length: int


@dataclass(frozen=True)
class SourcePlan:
    message_id: int
    spans: tuple[Span, ...]
    previous_id: int | None
    previous_chars: int


def merge_spans(spans: Iterable[Span], length: int | None = None) -> list[Span]:
    """Nối các khoảng chồng hoặc liền nhau. Có `length`: cắt về độ dài thân mail, bỏ khoảng nằm
    hẳn ngoài; khoảng rỗng chỉ giữ khi không còn khoảng nào (khi đó trả `[(0, 0)]` nếu có
    `length`)."""
    items: list[Span] = []
    for start, end in spans:
        if length is not None:
            end = min(end, length)
            if start >= length:
                continue
        if end <= start:
            continue
        items.append((start, end))
    items.sort()
    merged: list[Span] = []
    for start, end in items:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    if not merged:
        return [(0, 0)] if length is not None else []
    return merged


def group_sources(picked: Sequence[ChunkKey]) -> list[SourceSpans]:
    """Mỗi mail một nguồn, thứ tự theo đoạn đứng đầu của mail trong `picked`."""
    by_message: dict[int, list[Span]] = {}
    for message_id, start, end in picked:
        by_message.setdefault(message_id, []).append((start, end))
    return [SourceSpans(mid, tuple(merge_spans(spans))) for mid, spans in by_message.items()]


def expand_spans(spans: Sequence[Span], length: int, limit: int) -> list[Span]:
    """Nới các khoảng tới tổng `limit` ký tự. Mail không dài hơn `limit` thì lấy cả mail.
    Phần nới chia đều cho các khoảng, mỗi khoảng chia đều hai phía; phần dư do chạm đầu hoặc
    cuối mail không chia lại."""
    if length <= limit:
        return [(0, length)]
    base = merge_spans(spans, length)
    extra = limit - sum(e - s for s, e in base)
    if extra <= 0:
        return base
    side = extra // len(base) // 2
    return merge_spans([(max(0, s - side), min(length, e + side)) for s, e in base], length)


def render_body(body: str, spans: Sequence[Span]) -> str:
    """Ghép các khoảng của thân mail, cách nhau bằng dòng `…`."""
    return ELLIPSIS_SEPARATOR.join(body[s:e] for s, e in spans)


def plan_context(
    sources: Sequence[SourceSpans],
    lengths: Mapping[int, int],
    previous: Mapping[int, PreviousMail],
) -> list[SourcePlan]:
    """Lớp đảm bảo: mọi khoảng đã chọn đều được giữ (kể cả khi tổng vượt `CONTEXT_MAX_CHARS`,
    khi đó không nới gì). Lớp mở rộng: ngân sách còn lại cấp theo thứ tự nguồn; dưới
    `MIN_EXPANSION_CHARS` thì dừng."""
    base: list[tuple[SourceSpans, tuple[Span, ...]]] = []
    for src in sources:
        length = lengths.get(src.message_id)
        if length is None:
            length = max((e for _, e in src.spans), default=0)
        base.append((src, tuple(merge_spans(src.spans, length))))

    remaining = CONTEXT_MAX_CHARS - sum(e - s for _, spans in base for s, e in spans)
    used = {src.message_id for src, _ in base}
    plans: list[SourcePlan] = []
    for src, spans in base:
        previous_id: int | None = None
        previous_chars = 0
        if remaining >= MIN_EXPANSION_CHARS:
            length = lengths.get(src.message_id)
            if length is None:
                length = max((e for _, e in spans), default=0)
            covered = sum(e - s for s, e in spans)
            limit = min(SOURCE_MAX_CHARS, covered + remaining)
            expanded = tuple(expand_spans(spans, length, limit))
            extra = sum(e - s for s, e in expanded) - covered
            if extra > 0:
                spans = expanded
                remaining -= extra
            prev = previous.get(src.message_id)
            if (
                prev is not None
                and prev.message_id not in used
                and remaining >= MIN_EXPANSION_CHARS
            ):
                chars = min(PREVIOUS_MAX_CHARS, prev.length, remaining)
                if chars > 0:
                    previous_id = prev.message_id
                    previous_chars = chars
                    used.add(prev.message_id)
                    remaining -= chars
        plans.append(SourcePlan(src.message_id, spans, previous_id, previous_chars))
    return plans
