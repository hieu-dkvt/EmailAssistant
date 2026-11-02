"""So khớp glossary (spec 26 mục 9). Dữ liệu giả."""

import unicodedata
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.glossary import GlossaryEntry, load_entries, match_glossary, nfkc_map, normalize_term
from app.db.session import create_api_engine, user_session
from tests.dbutil import add_glossary, add_message, add_project, add_user, url_for_test_db


def e(term: str, translation: str, project_id: int | None = None) -> GlossaryEntry:
    return GlossaryEntry(term, normalize_term(term), translation, None, project_id)


def test_normalize_term() -> None:
    assert normalize_term(" Ｕnit  Test ") == "unittest"


def test_longest_match_no_overlap() -> None:
    es = [e("テスト", "kiểm thử"), e("結合テスト", "kiểm thử tích hợp")]
    assert [m.term for m in match_glossary("結合テストを実施", es)] == ["結合テスト"]


def test_short_term_kept_when_also_alone() -> None:
    es = [e("テスト", "kiểm thử"), e("結合テスト", "kiểm thử tích hợp")]
    got = {m.term for m in match_glossary("結合テストとテスト", es)}
    assert got == {"結合テスト", "テスト"}


def test_latin_word_boundary() -> None:
    assert match_glossary("RAPID build", [e("API", "API")]) == []
    assert len(match_glossary("REST API です", [e("API", "API")])) == 1
    assert len(match_glossary("(API)", [e("API", "API")])) == 1


def test_nfkc_and_case() -> None:
    assert len(match_glossary("ＡＰＩ仕様", [e("api", "API")])) == 1


@pytest.mark.parametrize(
    ("text_", "term"),
    [
        pytest.param("ﾃﾞｰﾀ移行を実施します", "データ移行", id="half-width-katakana"),
        pytest.param(
            unicodedata.normalize("NFD", "データ移行を実施します"), "データ移行", id="nfd-japanese"
        ),
        pytest.param(
            unicodedata.normalize("NFD", "đã xong kiểm thử tích hợp rồi"),
            "kiểm thử tích hợp",
            id="nfd-vietnamese",
        ),
    ],
)
def test_matches_across_unicode_forms(text_: str, term: str) -> None:
    assert [m.term for m in match_glossary(text_, [e(term, "x")])] == [term]


def test_word_boundary_sees_combined_letter_in_nfd_text() -> None:
    """Dấu tổ hợp ngay trước thuật ngữ thuộc về chữ Latin đứng trước, không phải ranh giới từ."""
    assert match_glossary(unicodedata.normalize("NFD", "éAPI"), [e("API", "x")]) == []
    assert len(match_glossary(unicodedata.normalize("NFD", "é API"), [e("API", "x")])) == 1


def test_glossary_matches_person_name_in_raw_text() -> None:
    """Thuật ngữ là tên người khớp được (trước đây nhãn che chặn)."""
    got = match_glossary("担当の小野寺様にご確認ください", [e("小野寺", "Onodera")])
    assert [m.term for m in got] == ["小野寺"]


def test_label_like_text_is_ordinary_text() -> None:
    """Chuỗi dạng [ORG_1] không còn là nhãn đặc biệt: khớp như mọi đoạn text khác."""
    assert len(match_glossary("[ORG_1]様", [e("ORG", "x")])) == 1
    assert len(match_glossary("ﾃﾞｰﾀ[ORG_1]", [e("ORG", "x")])) == 1


def test_nfkc_map_positions() -> None:
    norm, starts, ends = nfkc_map("ｶﾞ㈱Ａ")
    assert norm == "ガ(株)A"
    assert starts == [0, 2, 2, 2, 3] and ends == [2, 3, 3, 3, 4]
    nfd = unicodedata.normalize("NFD", "ế")
    assert nfkc_map(nfd + "x")[0] == unicodedata.normalize("NFKC", nfd) + "x"


def test_term_with_space_matches_text_with_space() -> None:
    assert len(match_glossary("run Unit Test now", [e("unit test", "kiểm thử đơn vị")])) == 1


def test_project_beats_common() -> None:
    es = [e("検収", "nghiệm thu"), e("検収", "chấp nhận", project_id=1)]
    m = match_glossary("検収", es)[0]
    assert m.translations == ("chấp nhận",) and not m.conflict


def test_two_projects_conflict() -> None:
    es = [e("検収", "A", project_id=1), e("検収", "B", project_id=2)]
    m = match_glossary("検収", es)[0]
    assert m.conflict and set(m.translations) == {"A", "B"}


def test_same_translation_in_two_projects_is_not_conflict() -> None:
    es = [e("検収", "A", project_id=1), e("検収", "A", project_id=2)]
    assert not match_glossary("検収", es)[0].conflict


def test_limit_prefers_longer_terms() -> None:
    es = [e("用語" + "あ" * n, f"t{n}") for n in range(70)]
    text_ = " ".join("用語" + "あ" * n + "。" for n in range(70))
    got = match_glossary(text_, es)
    assert len(got) == 60
    assert {m.term for m in got} == {"用語" + "あ" * n for n in range(10, 70)}


def test_empty_inputs() -> None:
    assert match_glossary("", [e("API", "API")]) == []
    assert match_glossary("API", []) == []


def test_many_occurrences_stay_bounded() -> None:
    assert len(match_glossary("テ" * 12_000, [e("テテ", "x")])) == 1


@pytest.fixture
async def api() -> AsyncIterator[AsyncEngine]:
    engine = create_api_engine(url_for_test_db("api"))
    yield engine
    await engine.dispose()


async def test_load_entries_scopes(owner_engine: Engine, clean: None, api: AsyncEngine) -> None:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        p1 = add_project(conn, a, "P1")
        p2 = add_project(conn, a, "P2")
        pb = add_project(conn, b, "PB")
        m = add_message(conn, "h1", user_id=a)
        conn.execute(
            text(
                "insert into message_projects (user_id, message_id, project_id) values (:u,:m,:p)"
            ),
            {"u": a, "m": m, "p": p1},
        )
        add_glossary(conn, a, "共通", "chung")
        add_glossary(conn, a, "一", "p1", project_id=p1)
        add_glossary(conn, a, "二", "p2", project_id=p2)
        add_glossary(conn, b, "他", "other")
        add_glossary(conn, b, "他P", "other", project_id=pb)
    async with user_session(api, a) as s:
        got = await load_entries(s, a, m)
    assert sorted(x.term for x in got) == ["一", "共通"]
