"""Chọn ngôn ngữ, chia đoạn, và NER thật (GiNZA, spaCy en) trên câu giả *.example.*."""

from collections.abc import Iterator

import pytest

from app.ner import SpacyRecognizer, language_blocks, language_of, segments


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("お世話になっております。田中です。", "ja"),
        ("ｶﾀｶﾅ only", "ja"),
        ("Hi Brian, thanks for the quote.", "en"),
        ("Anh Hiếu, em gửi báo cáo kết quả kiểm thử.", None),
        ("12345 --- 678", None),
        ("", None),
    ],
)
def test_language_of(text: str, lang: str | None) -> None:
    assert language_of(text) == lang


def test_segments_cover_text_and_cut_at_newline() -> None:
    text = ("a" * 30 + "\n") * 10
    parts = segments(text, limit=100)
    assert "".join(p for _, p in parts) == text
    assert all(len(p) <= 100 for _, p in parts)
    assert all(p.endswith("\n") for _, p in parts)
    assert [o for o, _ in parts] == [0, 93, 186, 279][: len(parts)]


def test_segments_long_line_hard_cut() -> None:
    text = "x" * 250
    assert [(o, len(p)) for o, p in segments(text, limit=100)] == [(0, 100), (100, 100), (200, 50)]


@pytest.fixture(scope="module")
def recognizer() -> Iterator[SpacyRecognizer]:
    yield SpacyRecognizer("ja_ginza", "en_core_web_sm")


def _found(r: SpacyRecognizer, text: str) -> set[tuple[str, str]]:
    (ents,) = r.entities([text])
    return {(text[e.start : e.end], e.label) for e in ents}


def test_ner_japanese_person_and_company(recognizer: SpacyRecognizer) -> None:
    text = "株式会社サンプルAの田中健二です。\n山本大輔さんにもよろしくお伝えください。"
    found = _found(recognizer, text)
    labels = {label for _, label in found}
    assert "PERSON" in labels
    assert any("田中" in s for s, label in found if label == "PERSON")


def test_ner_english(recognizer: SpacyRecognizer) -> None:
    found = _found(recognizer, "Brian Carter from Vendor C Inc. sent the renewal quote.")
    assert ("Brian Carter", "PERSON") in found


def test_ner_offsets_after_segment_split(recognizer: SpacyRecognizer) -> None:
    filler = "テスト結果を報告します。\n" * 1200  # > 1 đoạn
    text = filler + "担当は田中健二です。\n"
    (ents,) = recognizer.entities([text])
    assert any(text[e.start : e.end].startswith("田中") for e in ents if e.label == "PERSON")


def test_ner_skips_vietnamese(recognizer: SpacyRecognizer) -> None:
    assert recognizer.entities(["Anh Hiếu, em là Nguyễn Văn An."]) == [[]]


def test_language_blocks_per_line() -> None:
    text = (
        "Subject: 【至急】確認\nHi Brian,\nthanks for the quote.\n"
        "Anh Hiếu ơi, em gửi.\n\n田中です。"
    )
    blocks = [(lang, block) for _, block, lang in language_blocks(text)]
    assert blocks == [
        ("ja", "Subject: 【至急】確認\n"),
        ("en", "Hi Brian,\nthanks for the quote.\n"),
        ("ja", "田中です。"),
    ]
    for start, block, _ in language_blocks(text):
        assert text[start : start + len(block)] == block


def test_ner_reads_english_lines_in_japanese_mail(recognizer: SpacyRecognizer) -> None:
    text = "Subject: 【至急】ライセンス更新\nBrian Carter from Vendor C Inc. sent the quote."
    found = _found(recognizer, text)
    assert ("Brian Carter", "PERSON") in found


def test_english_line_with_vietnamese_name_runs_english_ner() -> None:
    text = "Please ask John Smith and Nguyễn Văn Hiếu about the schedule.\nAnh Hiếu ơi, em gửi nhé."
    assert [lang for _, _, lang in language_blocks(text)] == ["en"]
