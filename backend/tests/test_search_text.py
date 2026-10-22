"""Cú pháp ô tìm kiếm và làm nổi bật từ khóa (spec 14 mục 4, 6). Hàm thuần, không cần DB."""

import pytest

from app.search.highlight import HIGHLIGHT_LIMIT, find_spans, highlight, snippet
from app.search.query import MAX_QUERY_CHARS, QueryError, parse_query
from app.threads_view import split_parts
from app.web_schemas import Segment


@pytest.mark.parametrize(
    ("raw", "groonga", "keywords"),
    [
        ("見積", '"見積"', ("見積",)),
        ("見積  仕様書", '"見積" "仕様書"', ("見積", "仕様書")),
        ("見積　仕様書", '"見積" "仕様書"', ("見積", "仕様書")),  # khoảng trắng full-width
        ("見積 OR 請求", '"見積" OR "請求"', ("見積", "請求")),
        ("見積 ＯＲ 請求", '"見積" OR "請求"', ("見積", "請求")),  # NFKC
        ("見積 or 請求", '"見積" "or" "請求"', ("見積", "or", "請求")),  # chỉ OR viết hoa
        ("見積 -工数", '"見積" -"工数"', ("見積",)),
        ('-"Project B" 見積', '"見積" -"Project B"', ("見積",)),
        ('"Project B" 見積', '"Project B" "見積"', ("Project B", "見積")),
        ('"Project B', '"Project B"', ("Project B",)),  # thiếu dấu đóng
        ("OR 見積 OR", '"見積"', ("見積",)),
        ("見積 OR OR 請求", '"見積" OR "請求"', ("見積", "請求")),
        ("見積 OR -工数", '"見積" -"工数"', ("見積",)),
        ("a - b", '"a" "b"', ("a", "b")),
        ('say "hi\\"', '"say" "hi\\\\"', ("say", "hi\\")),
        ("Re: 見積", '"Re:" "見積"', ("Re:", "見積")),
        ("subject:abc *", '"subject:abc" "*"', ("subject:abc", "*")),
        ("Báo Báo báo", '"Báo" "Báo" "báo"', ("Báo",)),
    ],
)
def test_parse_query(raw: str, groonga: str, keywords: tuple[str, ...]) -> None:
    parsed = parse_query(raw)
    assert parsed is not None
    assert (parsed.groonga, parsed.keywords) == (groonga, keywords)


@pytest.mark.parametrize("raw", [None, "", "   ", '""', "OR", "-"])
def test_parse_query_empty(raw: str | None) -> None:
    assert parse_query(raw) is None


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("-工数", "query_needs_positive_term"),
        ("-a -b", "query_needs_positive_term"),
        ("x" * (MAX_QUERY_CHARS + 1), "query_too_long"),
        (" ".join(f"w{i}" for i in range(21)), "query_too_long"),
    ],
)
def test_parse_query_errors(raw: str, code: str) -> None:
    with pytest.raises(QueryError) as ei:
        parse_query(raw)
    assert ei.value.code == code


def hits(segments: list[Segment]) -> list[str]:
    return [s.text for s in segments if s.hit]


def test_highlight_basic() -> None:
    segs = highlight("お見積りの見積書", ["見積"])
    assert [(s.text, s.hit) for s in segs] == [
        ("お", False),
        ("見積", True),
        ("りの", False),
        ("見積", True),
        ("書", False),
    ]
    assert "".join(s.text for s in segs) == "お見積りの見積書"
    assert highlight("abc", []) == [Segment(text="abc")]
    assert highlight("", ["a"]) == [] and highlight(None, ["a"]) == []


def test_highlight_normalizes_like_index() -> None:
    assert hits(highlight("Invoice INVOICE invoice", ["invoice"])) == [
        "Invoice",
        "INVOICE",
        "invoice",
    ]
    # Full-width / half-width trong mail vẫn được tô khi gõ dạng thường.
    assert hits(highlight("件名：ＲＥ　ＰＲＪ－１２３", ["prj-123"])) == ["ＰＲＪ－１２３"]
    assert hits(highlight("ｶﾀｶﾅ テスト", ["カタカナ"])) == ["ｶﾀｶﾅ"]
    assert hits(highlight("Báo giá", ["BÁO"])) == ["Báo"]


def test_highlight_after_non_bmp_character() -> None:
    # 𠮷 là 1 code point trong Python nhưng 2 đơn vị UTF-16 trong JavaScript: trả đoạn, không trả
    # vị trí, nên web ghép lại đúng.
    segs = highlight("𠮷野家の見積", ["見積"])
    assert [(s.text, s.hit) for s in segs] == [("𠮷野家の", False), ("見積", True)]


def test_highlight_merges_overlaps() -> None:
    assert find_spans("abcdef", ["abc", "bcd", "ef"]) == [(0, 6)]  # chồng nhau hoặc liền nhau
    assert find_spans("abcdefgh", ["abc", "bc", "gh"]) == [(0, 3), (6, 8)]
    assert hits(highlight("abcdef", ["abc", "bcd"])) == ["abcd"]


def test_highlight_limit() -> None:
    text = "x" * HIGHLIGHT_LIMIT + "見積"
    segs = highlight(text, ["見積"])
    assert hits(segs) == [] and "".join(s.text for s in segs) == text


def test_snippet_window_and_ellipsis() -> None:
    body = "前置き\n" + "あ" * 100 + "見積" + "い" * 300
    segs = snippet(body, ["見積"])
    assert segs[0] == Segment(text="…") and segs[-1] == Segment(text="…")
    assert hits(segs) == ["見積"]
    text = "".join(s.text for s in segs)
    assert "\n" not in text and len(text) <= 60 + 2 + 140 + 2
    # Không khớp trong nội dung: đầu văn bản.
    segs = snippet("Xin chào\n\nanh", ["見積"])
    assert [(s.text, s.hit) for s in segs] == [("Xin chào anh", False)]
    assert snippet("", ["a"]) == []


def test_split_parts_by_cuts() -> None:
    body = "Hello\n> quoted\n--\nsig"
    cuts = [
        {"kind": "signature", "start": 15, "end": 21},
        {"kind": "quote", "start": 6, "end": 15},
    ]
    assert split_parts(body, cuts) == [
        ("body", "Hello\n"),
        ("quote", "> quoted\n"),
        ("signature", "--\nsig"),
    ]
    # Cut hỏng (ngoài phạm vi, chồng nhau, kiểu lạ) bị bỏ qua.
    bad = [
        {"kind": "quote", "start": 0, "end": 999},
        {"kind": "x", "start": 0, "end": 2},
        {"kind": "quote", "start": 3, "end": 2},
        "rác",
    ]
    assert split_parts(body, bad) == [("body", body)]
    assert split_parts(body, None) == [("body", body)]
