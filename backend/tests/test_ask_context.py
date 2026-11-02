"""Chia ngân sách ngữ cảnh hỏi đáp: hàm thuần (spec 27 mục 6)."""

from app.ai.context import (
    CONTEXT_MAX_CHARS,
    MIN_EXPANSION_CHARS,
    PreviousMail,
    SourceSpans,
    expand_spans,
    group_sources,
    merge_spans,
    plan_context,
    render_body,
)


def contains(spans, span):
    return any(s <= span[0] and span[1] <= e for s, e in spans)


def covered(plan):
    return sum(e - s for s, e in plan.spans)


def test_merge_spans():
    assert merge_spans([(0, 100), (80, 200), (200, 250), (300, 400)]) == [(0, 250), (300, 400)]


def test_merge_unsorted_and_empty():
    assert merge_spans([(300, 400), (0, 100)]) == [(0, 100), (300, 400)]
    assert merge_spans([]) == []


def test_merge_clamps_stale_positions():
    assert merge_spans([(900, 1700), (2000, 2100)], length=1000) == [(900, 1000)]


def test_merge_keeps_one_span_when_all_stale():
    assert merge_spans([(1200, 1300)], length=1000) == [(0, 0)]
    assert merge_spans([], length=1000) == [(0, 0)]


def test_group_keeps_first_rank_order():
    got = group_sources([(2, 0, 800), (1, 0, 800), (2, 700, 1500)])
    assert got == [SourceSpans(2, ((0, 1500),)), SourceSpans(1, ((0, 800),))]


def test_expand_short_mail_is_whole():
    assert expand_spans([(10, 20)], 500, 3000) == [(0, 500)]


def test_expand_long_mail_even_both_sides():
    assert expand_spans([(5000, 5800)], 10_000, 3000) == [(3900, 6900)]


def test_expand_clamped_leftover_not_reused():
    assert expand_spans([(1000, 1800)], 10_000, 3000) == [(0, 2900)]


def test_expand_empty_body():
    assert expand_spans([(0, 0)], 0, 3000) == [(0, 0)]


def test_expand_limit_below_covered_keeps_spans():
    assert expand_spans([(100, 1100)], 10_000, 500) == [(100, 1100)]


def test_expand_multiple_spans_merge_and_keep_originals():
    got = expand_spans([(1000, 1800), (2000, 2800)], 10_000, 3000)
    assert contains(got, (1000, 1800)) and contains(got, (2000, 2800))
    assert sum(e - s for s, e in got) <= 3000


def test_render_body_joins_with_ellipsis_line_and_counts_code_points():
    assert render_body("a😀cdef", [(0, 2), (4, 6)]) == "a😀\n…\nef"


def test_budget_by_rank_keeps_every_span():
    n = 20
    sources = [SourceSpans(i, ((5000, 5800),)) for i in range(n)]
    lengths = dict.fromkeys(range(n), 10_000)
    previous = {i: PreviousMail(100 + i, 5000) for i in range(n)}
    plans = plan_context(sources, lengths, previous)
    assert [covered(p) for p in plans][:4] == [3000, 3000, 2000, 800]
    assert [p.previous_chars for p in plans][:4] == [1200, 1200, 0, 0]
    assert all(contains(p.spans, (5000, 5800)) for p in plans)
    total = sum(covered(p) + p.previous_chars for p in plans)
    assert total <= CONTEXT_MAX_CHARS


def test_stops_below_min_expansion():
    # giữ đủ đoạn thì còn 199 ký tự: không nới, không thêm mail trước
    sources = [SourceSpans(i, ((0, 1000),)) for i in range(23)] + [SourceSpans(23, ((0, 801),))]
    assert 23 * 1000 + 801 == CONTEXT_MAX_CHARS - (MIN_EXPANSION_CHARS - 1)
    lengths = dict.fromkeys(range(24), 10_000)
    previous = {i: PreviousMail(100 + i, 500) for i in range(24)}
    plans = plan_context(sources, lengths, previous)
    assert [covered(p) for p in plans] == [1000] * 23 + [801]
    assert all(p.previous_id is None and p.previous_chars == 0 for p in plans)


def test_expands_when_exactly_min_expansion_left():
    sources = [SourceSpans(1, ((5000, 5800),))] + [
        SourceSpans(i, ((0, 1000),)) for i in range(2, 25)
    ]
    assert 800 + 23 * 1000 == CONTEXT_MAX_CHARS - MIN_EXPANSION_CHARS
    plans = plan_context(sources, dict.fromkeys(range(1, 25), 10_000), {})
    assert covered(plans[0]) == 1000


def test_plan_clamps_stale_spans_to_body_length():
    plans = plan_context([SourceSpans(1, ((900, 1700),))], {1: 1000}, {})
    assert plans[0].spans == ((0, 1000),)


def test_previous_skipped_when_already_source_or_used():
    sources = [
        SourceSpans(1, ((0, 100),)),
        SourceSpans(2, ((0, 100),)),
        SourceSpans(3, ((0, 100),)),
    ]
    lengths = {1: 100, 2: 100, 3: 100}
    previous = {1: PreviousMail(50, 300), 2: PreviousMail(50, 300), 3: PreviousMail(1, 300)}
    plans = plan_context(sources, lengths, previous)
    assert [p.previous_id for p in plans] == [50, None, None]
    assert [p.previous_chars for p in plans] == [300, 0, 0]


def test_subject_only_source_can_have_previous():
    plans = plan_context([SourceSpans(1, ((0, 0),))], {1: 0}, {1: PreviousMail(2, 300)})
    assert plans[0].spans == ((0, 0),) and plans[0].previous_chars == 300


def test_previous_capped_at_previous_max_and_own_length():
    plans = plan_context(
        [SourceSpans(1, ((0, 10),)), SourceSpans(2, ((0, 10),))],
        {1: 10, 2: 10},
        {1: PreviousMail(8, 5000), 2: PreviousMail(9, 40)},
    )
    assert [p.previous_chars for p in plans] == [1200, 40]


def test_empty_sources():
    assert plan_context([], {}, {}) == []


def test_guaranteed_spans_exceed_max_are_all_kept():
    n = 40
    sources = [SourceSpans(i, ((0, 800),)) for i in range(n)]  # 32.000 > 24.000
    plans = plan_context(sources, dict.fromkeys(range(n), 10_000), {})
    assert all(p.spans == ((0, 800),) for p in plans)
