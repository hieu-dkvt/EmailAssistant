"""Nhận dạng tên riêng (NER) cho bước che thông tin trước khi gửi AI (spec 20 mục 4.4).

- Văn bản có chữ Nhật (kana/kanji): GiNZA (ja_ginza, spaCy + SudachiPy).
- Văn bản không có chữ Nhật và không phải tiếng Việt: spaCy en_core_web_sm.
- Tiếng Việt: không có model, bỏ qua (backend che bằng từ điển người gửi/nhận và mẫu).

Trả vị trí theo code point (giống chuỗi Python của backend), nhãn đã quy về PERSON, ORG, ADDRESS,
PHONE, EMAIL, URL, MONEY (backend đã có mẫu regex cho bốn nhãn sau; NER là lớp bổ sung).
Không trả lại text, không log text.
"""

import re
import threading
from dataclasses import dataclass
from typing import Protocol

# Nhãn ENE của GiNZA (ja_ginza 5.3, đã kiểm danh sách nhãn của pipe "ner") → nhãn của dự án.
# Nhãn khác (ngày, số lượng, mã số, tỉnh/thành phố…) không che: không nhận dạng một người hay một
# khách hàng, và AI cần chúng để trả lời (vd. ID_Number hay gặp ở mã lỗi E1024).
JA_LABELS = {
    "Person": "PERSON",
    "Company": "ORG",
    "Company_Group": "ORG",
    "Corporation_Other": "ORG",
    "Organization_Other": "ORG",
    "International_Organization": "ORG",
    "Political_Organization_Other": "ORG",
    "Political_Party": "ORG",
    "Government": "ORG",
    "Public_Institution": "ORG",
    "Military": "ORG",
    "Show_Organization": "ORG",
    "Sports_Organization_Other": "ORG",
    "Pro_Sports_Organization": "ORG",
    "School": "ORG",
    "Research_Institute": "ORG",
    "Postal_Address": "ADDRESS",
    "Phone_Number": "PHONE",
    "Email": "EMAIL",
    "URL": "URL",
    "Money": "MONEY",
}
EN_LABELS = {"PERSON": "PERSON", "ORG": "ORG", "MONEY": "MONEY"}

# Sudachi từ chối input dài hơn ~49 KB: chia theo dòng thành đoạn tối đa SEGMENT_CHARS ký tự.
SEGMENT_CHARS = 10_000

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")
_LATIN_LETTER = re.compile(r"[A-Za-zÀ-ỹ]")
# Chữ chỉ có trong tiếng Việt (không có trong tiếng Anh, Pháp…).
_VI_LETTER = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
    r"ẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ]"
)


@dataclass(frozen=True)
class Entity:
    start: int
    end: int
    label: str  # PERSON | ORG | ADDRESS | PHONE | EMAIL | URL | MONEY


class Recognizer(Protocol):
    models: list[str]

    def entities(self, texts: list[str]) -> list[list[Entity]]: ...


VI_RATIO_TEXT = 0.02
# Một dòng tiếng Anh có tên Việt có dấu ("ask John Smith and Nguyễn Văn Hiếu") có tỷ lệ ~0,06;
# dòng tiếng Việt thật ~0,15 trở lên.
VI_RATIO_LINE = 0.1


def language_of(text: str, vi_ratio: float = VI_RATIO_TEXT) -> str | None:
    """'ja', 'en' hoặc None (tiếng Việt / không có chữ): chọn model NER cho văn bản."""
    if _CJK.search(text):
        return "ja"
    letters = len(_LATIN_LETTER.findall(text))
    if letters == 0:
        return None
    if len(_VI_LETTER.findall(text)) / letters > vi_ratio:
        return None
    return "en"


def language_blocks(text: str) -> list[tuple[int, str, str]]:
    """Chia text theo dòng thành khối cùng ngôn ngữ: [(vị trí, khối, 'ja' | 'en')].

    Chọn model theo từng dòng, không theo cả text: subject 【至急】 không làm cả mail tiếng Anh
    chạy GiNZA, đoạn tiếng Anh trích trong mail tiếng Việt vẫn được nhận. Dòng tiếng Việt bị bỏ
    qua (không có model); dòng không có chữ (trống, số) đi theo khối đang mở.
    """
    blocks: list[tuple[int, str, str]] = []
    cur_start, cur_lang, cur_parts = 0, "", []
    pos = 0
    for line in text.splitlines(keepends=True):
        if _CJK.search(line):
            lang = "ja"
        elif not _LATIN_LETTER.search(line):
            lang = cur_lang  # dòng trung tính
        else:
            lang = language_of(line, VI_RATIO_LINE) or ""
        if lang != cur_lang:
            if cur_lang and cur_parts:
                blocks.append((cur_start, "".join(cur_parts), cur_lang))
            cur_start, cur_lang, cur_parts = pos, lang, []
        cur_parts.append(line)
        pos += len(line)
    if cur_lang and cur_parts:
        blocks.append((cur_start, "".join(cur_parts), cur_lang))
    return blocks


def segments(text: str, limit: int = SEGMENT_CHARS) -> list[tuple[int, str]]:
    """Chia text thành đoạn ≤ limit ký tự, cắt ở cuối dòng nếu được: [(vị trí bắt đầu, đoạn)]."""
    out: list[tuple[int, str]] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + limit, n)
        if end < n:
            cut = text.rfind("\n", start + limit // 2, end)
            if cut != -1:
                end = cut + 1
        out.append((start, text[start:end]))
        start = end
    return out


class SpacyRecognizer:
    def __init__(self, ja_model: str, en_model: str) -> None:
        import spacy

        # Chỉ cần NER (tok2vec + ner): tắt thành phần khác cho nhanh (parser của GiNZA nặng nhất).
        # Đã đo trên text giả: kết quả NER như khi chạy thêm morphologizer.
        self._ja = spacy.load(
            ja_model,
            exclude=[
                "parser",
                "compound_splitter",
                "bunsetu_recognizer",
                "morphologizer",
                "attribute_ruler",
            ],
        )
        self._en = spacy.load(en_model, exclude=["parser", "lemmatizer", "tagger"])
        # Model khác (nhãn khác bảng ánh xạ) sẽ lặng lẽ không nhận ra ai: từ chối chạy.
        for nlp, needed in ((self._ja, {"Person", "Company"}), (self._en, {"PERSON", "ORG"})):
            pipe = nlp.get_pipe("ner") if "ner" in nlp.pipe_names else None
            labels = set(getattr(pipe, "labels", ()))
            if not needed <= labels:
                raise RuntimeError(f"model NER {nlp.meta.get('name')} thiếu nhãn {needed - labels}")
        self.models = [ja_model, en_model]
        self._lock = threading.Lock()

    def entities(self, texts: list[str]) -> list[list[Entity]]:
        with self._lock:
            return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[Entity]:
        found: list[Entity] = []
        for block_start, block, lang in language_blocks(text):
            nlp, labels = (self._ja, JA_LABELS) if lang == "ja" else (self._en, EN_LABELS)
            for offset, part in segments(block):
                for ent in nlp(part).ents:
                    label = labels.get(ent.label_)
                    if label and ent.end_char > ent.start_char:
                        base = block_start + offset
                        found.append(Entity(base + ent.start_char, base + ent.end_char, label))
        return found
