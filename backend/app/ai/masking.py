"""Che thông tin nhạy cảm trước khi gửi Claude, thay ngược khi nhận về (spec 20).

Một Masker cho một lần hỏi: mọi text (câu hỏi, đoạn mail, header) dùng chung một bảng nhãn nên
cùng thực thể luôn cùng nhãn. Bảng nhãn chỉ nằm trong bộ nhớ. Lỗi ở bất kỳ bước nào (kể cả NER)
được ném ra: bên gọi không được gửi text chưa che (fail closed). Không log text.

Mẫu và từ điển chạy trên bản NFKC của text (chữ full-width/half-width, ＠, ０-９ về dạng thường),
vị trí được đổi về text gốc qua bảng ánh xạ. Mọi lượng từ trong regex đều có giới hạn: text là
nội dung mail do người ngoài gửi, regex phải chạy tuyến tính (subject có thể dài 10.000 ký tự).
"""

import asyncio
import re
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from app.ai.names import Entity, company_core, find_companies, fold_latin
from app.ml_client import NerEntity

KINDS = ("EMAIL", "URL", "PHONE", "CONTRACT", "MONEY", "ADDRESS", "ORG", "PERSON")
_PRIORITY = {k: i for i, k in enumerate(KINDS)}
LABEL_RE = re.compile(r"\[(" + "|".join(KINDS) + r")_(\d+)\]")

type NerFn = Callable[[list[str]], Awaitable[list[list[NerEntity]]]]

# --- mẫu (chạy trên text đã NFKC: chữ số, chữ Latin, ký hiệu full-width đã về dạng ASCII) ---

_CJK = "㐀-䶿一-鿿"
EMAIL = re.compile(
    r"(?<![\w.%+\-])[A-Za-z0-9À-ỹ._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,8}"
    r"\.[A-Za-z]{2,24}"
)
_URL_TAIL = r"[^\s<>\"'）)】」、。]"
URL = re.compile(rf"(?:https?://|www\.){_URL_TAIL}{{1,2000}}", re.I)
_TLDS = (
    "com|net|org|edu|gov|jp|vn|io|co|info|biz|dev|app|cloud|ai|me|asia|tech|de|uk|fr|kr|cn|sg|"
    "th|id|au|ca|us|in|tw|hk|eu|xyz|online|site|ph|my|nz|ch|nl|it|es|se|ru|br|mx|la|kh|mm"
)
DOMAIN = re.compile(
    r"(?<![@\w.\-])(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.){1,10}"
    rf"(?:{_TLDS})(?![\w\-])(?:/{_URL_TAIL}{{0,500}})?",
    re.I,
)
# Tên công nghệ có dạng tên miền: không che.
_NOT_DOMAINS = {"asp.net", "vb.net", "ado.net"}
_SEP = r"[ \t\-‐ー.−–—]"
# Ranh giới của số điện thoại: không dính chữ/số khác; dấu chấm chỉ chặn khi nối với chữ số
# (số ở cuối câu "…0912345678." và sau "Tel." vẫn khớp; "1.2.3" thì không).
_PHONE_START = r"(?<![\w+])(?<!\d\.)"
_PHONE_END = r"(?!\w)(?!\.\d)"
PHONE = re.compile(
    rf"{_PHONE_START}(?:\(?\+\d{{1,3}}\)?{_SEP}?)?(?:\(\d{{1,4}}\){_SEP}?)?"
    rf"\d{{1,5}}(?:{_SEP}\d{{1,5}}){{1,4}}{_PHONE_END}"
    # Số liền không dấu cách: 0912345678, 09012345678, +84912345678.
    rf"|{_PHONE_START}(?:\+\d{{1,3}}\d{{8,12}}|0\d{{9,10}}){_PHONE_END}"
    # Kiểu Nhật 03(1234)5678.
    rf"|{_PHONE_START}\d{{2,4}}\(\d{{1,4}}\)\d{{3,4}}{_PHONE_END}"
)
_DATE_INSIDE = re.compile(r"\d{4}[\-/.]\d{1,2}[\-/.]\d{1,2}")
_NUM = r"[0-9][0-9,.]{0,24}"
_COUNTERS = r"件|人|個|回|時間|行|社|名|台|枚|本|部|通|日|年|ヶ月|か月|ページ|km|m"
MONEY = re.compile(
    rf"(?:[¥$€£]|US\$|USD|JPY|VND|EUR)\s?{_NUM}(?:\s?(?:万|億|千|triệu|tỷ|nghìn|[kKmM]\b))?"
    rf"|{_NUM}\s?(?:万|億|千)?\s?(?:円|ドル|ユーロ|USD|JPY|VND|VNĐ|vnđ|EUR|yen"
    r"|triệu đồng|tỷ đồng|trăm triệu|trăm nghìn|triệu|tỷ|tỉ|nghìn đồng|ngàn đồng|nghìn|ngàn|đồng"
    r"|đ(?![\wÀ-ỹ]))"
    rf"|[0-9一二三四五六七八九十百千万億兆,.]{{1,24}}円"
    rf"|{_NUM}\s?(?:万|億)(?!\s?(?:{_COUNTERS}))"
    rf"|{_NUM}\s?[kKmM]\s?(?:USD|JPY|VND|EUR|yen|円|\$|đ)"
)
# Số tiền đứng sau từ chỉ giá: "見積金額 1,200,000（税別）", "Chi phí: 35.000.000".
AMOUNT_AFTER_WORD = re.compile(
    r"(?:金額|価格|単価|費用|予算|見積額|合計|総額|請求額|giá|Giá|chi phí|Chi phí|ngân sách"
    r"|Ngân sách|tổng tiền|Tổng tiền|[Pp]rice|[Cc]ost|[Aa]mount|[Tt]otal|[Bb]udget)"
    r"\s{0,3}[:：は]?\s{0,3}(?:約|およそ|khoảng|about|approx\.?)?\s{0,3}"
    # Không lấy năm/ngày: "予算 2026年度", "Total 2026-10-12".
    r"(\d{1,3}(?:[,.]\d{3}){1,6}|\d{4,12})(?!\d)(?!\s?(?:年|年度)|[-/]\d)"
)
_CONTRACT_LABELS = (
    r"契約書?番号|契約書?No\.?|注文書?番号|注文No\.?|発注書?番号|発注No\.?|受注番号|見積書?番号"
    r"|見積書?No\.?|請求書?番号|請求書?No\.?|案件番号|管理番号|伝票番号"
    r"|P/?O\s?(?:No\.?|Number|#)|Contract\s?(?:No\.?|Number|#)|Order\s?(?:No\.?|Number|#)"
    r"|Invoice\s?(?:No\.?|Number|#)|Quot(?:e|ation)\s?(?:No\.?|Number|#)"
    r"|Số hợp đồng|Số HĐ|Mã hợp đồng|Mã HĐ|Hợp đồng số|HĐ số|Số đơn hàng|Mã đơn hàng|Số hóa đơn"
)
CONTRACT = re.compile(
    rf"(?:{_CONTRACT_LABELS})\s{{0,3}}[:#]?\s{{0,3}}([A-Za-z0-9][A-Za-z0-9\-_/#.]{{0,39}})",
    re.I,
)
# Mã dạng ABC-123 ngay sau từ chỉ hợp đồng/đơn hàng (không có nhãn "số"): "Hợp đồng HD-2026-0012".
CONTRACT_NEAR = re.compile(
    r"(?:契約|発注|注文|請求書|[Hh]ợp đồng|HĐ|PO|[Cc]ontract|[Oo]rder|[Ii]nvoice)[^\n]{0,8}?"
    r"(?<![A-Za-z0-9])([A-Z]{1,6}[-/]\d{2,8}(?:[-/]\d{1,8}){0,4})(?![A-Za-z0-9])"
)
POSTAL = re.compile(r"〒\s?\d{3}-?\d{4}[^\n]{0,80}")
JP_ADDRESS = re.compile(
    rf"(?:東京都|北海道|京都府|大阪府|[{_CJK}]{{2,3}}県)[^\s、。]{{0,10}}?[市区町村郡][^\n、。]{{0,40}}"
)
# Không có tên tỉnh: "港区芝公園4-2-8", "港区芝公園4丁目2番8号".
JP_ADDRESS_LOCAL = re.compile(
    rf"[{_CJK}]{{1,4}}[区市町村][{_CJK}ぁ-んァ-ヶ]{{1,8}}\d{{1,4}}"
    r"(?:(?:[-‐−の]\d{1,4}){1,3}|丁目(?:\d{1,4}番地?)?(?:\d{1,4}号)?)"
)
_VI_PLACE = (
    r"(?:[Pp]hường|[Qq]uận|[Hh]uyện|[Tt]hành phố|TP\.?|[Tt]ỉnh|Hà Nội|Hồ Chí Minh|HCM|Đà Nẵng)"
)
VI_ADDRESS = re.compile(
    rf"(?:(?:[Ss]ố|[Tt]ầng|[Ll]ầu|[Pp]hòng)\s{{1,3}}\d{{1,4}}|\d{{1,5}}[A-Z]?(?:/\d{{1,4}})?\s"
    rf"{{1,3}}[A-ZĐÀ-Ỹ])[^\n]{{0,80}}?{_VI_PLACE}[^\n]{{0,40}}"
    r"|\d{1,5}\s{1,3}(?:[Đđ]ường|[Pp]hố)\s[^\n]{1,60}"
)
LABELED_ADDRESS = re.compile(
    r"(?:住所|所在地|Địa chỉ|địa chỉ|[Aa]ddress)\s{0,3}[:：]\s{0,3}([^\n]{1,200})"
)

_HONORIFICS_JA = (
    "様|さま|さん|殿|氏|君|くん|ちゃん|先生|部長|課長|社長|専務|常務|係長|主任|室長|所長|次長|"
    "支店長"
)
# Tên đứng trước kính ngữ: 1–4 chữ Hán (họ + tên) hoặc katakana. Có thể dính chữ Hán đứng trước
# (che thừa, không lọt); Masker thu lại theo tên đã biết (_trim_to_known).
JA_HONORIFIC = re.compile(
    rf"([{_CJK}々]{{1,4}}[ァ-ヶー]{{0,8}}|[ァ-ヶー]{{2,10}})\s?({_HONORIFICS_JA})"
)  # họ Hán + tên katakana: "鈴木エリカ様"
# "仕様", "同様", "模様"…: chữ trước 様 làm thành từ thường, không phải tên. Chữ không bao giờ
# đứng cuối tên người: loại mọi khi. Chữ có thể là cuối tên (健一, 本多): chỉ loại khi đứng một
# mình ("一様", "多様"). "様式", "様子"…: 様 là đầu một từ khác.
_NOT_NAME_END_BEFORE_SAMA = set("仕同模異何斯皆殿")
_NOT_NAME_ALONE_BEFORE_SAMA = set("一多有神王左如")
_NOT_HONORIFIC_AFTER_SAMA = set("式子相々態")
_HONORIFIC_TAIL = re.compile(rf"\s?(?:{_HONORIFICS_JA})$")
LATIN_JA_HONORIFIC = re.compile(r"([A-ZÀ-ỸĐ][A-Za-zÀ-ỹđĐ]{1,15})\s?(?=さん|様|さま|殿|先生)")
ROMAJI_SUFFIX = re.compile(r"\b([A-Z][A-Za-z]{1,15})[- ](?i:san|sama|sensei|kun|chan|shi|dono)\b")
_NAME = r"[A-ZÀ-ỸĐ][A-Za-zÀ-ỹĐđ]{0,20}(?:[ \t]{1,3}[A-ZÀ-ỸĐ][A-Za-zÀ-ỹĐđ]{0,20}){0,3}"
_TITLES = (
    r"(?:[Aa]nh|[Cc]hị|[Ee]m|[Ôô]ng|[Bb]à|[Cc]ô|[Cc]hú|[Bb]ác|[Bb]ạn|Mr\.?|Ms\.?|Mrs\.?|Miss"
    r"|Dr\.?|MR\.?|MS\.?)"
)
_GREETINGS = (
    r"(?:[Hh]i|[Hh]ello|[Dd]ear|[Cc]hào|[Xx]in chào|[Kk]ính gửi|[Gg]ửi|[Tt]hanks|[Tt]hank you"
    r"|[Cc]ảm ơn)"
)
# Lời chào, có thể kèm danh xưng: "Chào Anh Tuấn", "Dear Mr. Tanaka", "Hi Hieu".
GREETING_NAME = re.compile(
    rf"(?<![\wÀ-ỹ]){_GREETINGS}[ \t]{{0,3}},?[ \t]{{1,3}}(?:{_TITLES}[ \t]{{1,3}})?({_NAME})"
)
TITLE_NAME = re.compile(rf"(?<![\wÀ-ỹ]){_TITLES}[ \t]{{1,3}}({_NAME})")
# Viết tắt a/c/e + tên, chỉ khi ngay sau là dấu câu, "ơi" hay hết dòng ("a Tuấn,", "c Lan ơi").
SHORT_TITLE_NAME = re.compile(
    rf"(?<![\wÀ-ỹ])[ace][ \t]{{1,3}}({_NAME})(?=[ \t]{{0,3}}(?:[,:!?]|ơi|$))", re.M
)
_NOT_NAMES_JA = {
    "皆", "各位", "客", "お客", "担当", "担当者", "関係者", "営業", "開発", "技術", "総務", "経理",
    "人事", "品質", "企画", "管理", "御社", "貴社", "弊社", "当社", "先方", "皆さ", "神", "王",
    "同", "本", "当", "貴", "御", "各", "全", "部", "課", "社", "店", "長",
}  # fmt: skip
_NOT_NAMES_LATIN = {
    "All", "Team", "Everyone", "There", "Sir", "Madam", "Customer", "Client", "Partner", "Anh",
    "Chị", "Em", "Mọi", "Cả", "The", "Our", "Your", "Đã", "OK", "Mr", "Ms", "Mrs", "Dr", "Miss",
    "Guys", "Bạn", "Các", "Cảm",
}  # fmt: skip


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str


def escape_labels(text: str) -> str:
    """Chuỗi giống nhãn có sẵn trong text gốc: đổi '[' thành '［' để không bị thay ngược nhầm."""
    return LABEL_RE.sub(lambda m: "［" + m.group(0)[1:], text)


_SEMI_VOICED = "ﾞﾟ"  # ﾞ ﾟ của katakana half-width (NFKC thành dấu tổ hợp)


def nfkc_map(text: str) -> tuple[str, list[int], list[int]]:
    """Bản NFKC của text và vị trí gốc [starts[i], ends[i]) của từng ký tự trong bản NFKC.

    Chuẩn hóa theo cụm: một ký tự gốc cùng các dấu tổ hợp theo sau (chữ Việt dạng NFD "ế" =
    e + ◌̂ + ◌́; ｶﾞ) thành một cụm, NFKC cả cụm (㈱ → (株), ０ → 0, ＠ → @, ｶﾞ → ガ), mọi ký tự
    sinh ra từ cụm đều trỏ về [đầu cụm, cuối cụm).
    """
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and (unicodedata.combining(text[j]) or text[j] in _SEMI_VOICED):
            j += 1
        for c in unicodedata.normalize("NFKC", text[i:j]):
            chars.append(c)
            starts.append(i)
            ends.append(j)
        i = j
    return "".join(chars), starts, ends


def pattern_spans(text: str) -> list[Span]:
    """Mọi khoảng nhận ra bằng mẫu trên text (đã NFKC), có thể chồng nhau."""
    spans: list[Span] = []

    def add(kind: str, start: int, end: int) -> None:
        if end > start:
            spans.append(Span(start, end, kind))

    for m in EMAIL.finditer(text):
        add("EMAIL", m.start(), m.end())
    for p in (URL, DOMAIN):
        for m in p.finditer(text):
            if m.group(0).casefold() not in _NOT_DOMAINS:
                add("URL", m.start(), m.end())
    for m in PHONE.finditer(text):
        digits = sum(c.isdigit() for c in m.group(0))
        if 9 <= digits <= 15 and not _DATE_INSIDE.search(m.group(0)):
            add("PHONE", m.start(), m.end())
    for p in (CONTRACT, CONTRACT_NEAR):
        for m in p.finditer(text):
            if len(m.group(1)) >= 2:
                add("CONTRACT", m.start(1), m.end(1))
    for m in MONEY.finditer(text):
        if any(c.isdigit() or c in "一二三四五六七八九十" for c in m.group(0)):
            add("MONEY", m.start(), m.end())
    for m in AMOUNT_AFTER_WORD.finditer(text):
        add("MONEY", m.start(1), m.end(1))
    for p in (POSTAL, JP_ADDRESS, JP_ADDRESS_LOCAL, VI_ADDRESS):
        for m in p.finditer(text):
            add("ADDRESS", m.start(), m.end())
    for m in LABELED_ADDRESS.finditer(text):
        add("ADDRESS", m.start(1), m.end(1))
    for s, e in find_companies(text):
        add("ORG", s, e)
    for m in JA_HONORIFIC.finditer(text):
        name, honorific = m.group(1), m.group(2)
        if name in _NOT_NAMES_JA:
            continue
        if honorific == "様" and (
            name[-1] in _NOT_NAME_END_BEFORE_SAMA
            or name in _NOT_NAME_ALONE_BEFORE_SAMA
            or text[m.end(2) : m.end(2) + 1] in _NOT_HONORIFIC_AFTER_SAMA
        ):
            continue
        add("PERSON", m.start(1), m.end(1))
    for p in (LATIN_JA_HONORIFIC, ROMAJI_SUFFIX):
        for m in p.finditer(text):
            if m.group(1) not in _NOT_NAMES_LATIN:
                add("PERSON", m.start(1), m.end(1))
    for p in (GREETING_NAME, TITLE_NAME, SHORT_TITLE_NAME):
        for m in p.finditer(text):
            words = m.group(1).split()
            while words and words[-1] in _NOT_NAMES_LATIN:
                words.pop()
            while words and words[0] in _NOT_NAMES_LATIN:
                words.pop(0)
            if words:
                name = " ".join(words)
                start = text.find(words[0], m.start(1))
                end = text.find(words[-1], start) + len(words[-1])
                if start >= 0 and end > start and len(name) >= 2:
                    add("PERSON", start, end)
    return spans


_LATIN_WORD = "A-Za-z0-9À-ỹĐđ"
# Từ của header / tiền tố subject mà NER (GiNZA) hay nhận nhầm là tổ chức.
_NOT_ENTITIES = {"from", "to", "cc", "bcc", "subject", "date", "re", "fw", "fwd", "sent"}
# Từ katakana của tên hệ thống/sản phẩm: "tên người" có chữ Hán + một trong các từ này là nhầm
# (GiNZA nhận "在庫管理システム" là tên người). Tên người thật trộn Hán + katakana ("鈴木エリカ")
# vẫn được che.
_SYSTEM_KATAKANA = (
    "システム", "サーバ", "データ", "ツール", "サービス", "アプリ", "ソフト", "ネットワーク",
    "プロジェクト", "メール", "ファイル", "レポート", "テスト", "ページ", "モジュール", "センター",
    "グループ", "チーム", "マネージャ", "プログラム", "エラー", "バッチ", "ログ", "デザイン",
)  # fmt: skip


def clean_ner_span(text: str, span: Span) -> Span | None:
    """Sửa / bỏ khoảng NER không đáng tin: bỏ kính ngữ dính vào tên ("田中様" → "田中"); bỏ "tên
    người" là tên hệ thống ("在庫管理システム") hay quá dài; bỏ từ header."""
    if text[span.start : span.end].strip(" :：").casefold() in _NOT_ENTITIES:
        return None
    if span.kind != "PERSON":
        return span
    surface = text[span.start : span.end]
    m = _HONORIFIC_TAIL.search(surface)
    end = span.end - (len(m.group(0)) if m else 0)
    surface = text[span.start : end].strip()
    if not surface:
        return None
    has_kanji = any("㐀" <= c <= "鿿" or c == "々" for c in surface)
    has_kana = any("぀" <= c <= "ヿ" or "ｦ" <= c <= "ﾟ" for c in surface)
    if has_kanji or has_kana:
        if len(surface) > 20:
            return None
        if has_kanji and any(w in surface for w in _SYSTEM_KATAKANA):
            return None
    elif len(surface) > 60:  # tên Latin dài ("Nguyen Thi Phuong Thao") vẫn che
        return None
    return Span(span.start, span.start + len(text[span.start : end].rstrip()), "PERSON")


def resolve(spans: Iterable[Span]) -> list[Span]:
    """Gộp khoảng chồng nhau cùng loại, rồi giữ khoảng không chồng nhau: dài hơn trước, bằng
    nhau theo thứ tự ưu tiên của KINDS. Kết quả sắp theo vị trí."""
    by_kind: dict[str, list[Span]] = {}
    for s in spans:
        by_kind.setdefault(s.kind, []).append(s)
    merged: list[Span] = []
    for kind, items in by_kind.items():
        items.sort(key=lambda x: (x.start, -x.end))
        cur = items[0]
        for s in items[1:]:
            if s.start < cur.end:
                cur = Span(cur.start, max(cur.end, s.end), kind)
            else:
                merged.append(cur)
                cur = s
        merged.append(cur)
    chosen: list[Span] = []
    for s in sorted(merged, key=lambda x: (x.start - x.end, _PRIORITY[x.kind], x.start)):
        if all(s.end <= c.start or s.start >= c.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda x: x.start)


_SHORT_NUMBER = re.compile(r"[\d,.\s\-/]+")


def _nfkc(surface: str) -> str:
    return unicodedata.normalize("NFKC", surface).strip()


def _norm(surface: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", surface)).casefold()


def _is_latin(surface: str) -> bool:
    return all(not ("぀" <= c <= "鿿") for c in surface)


def _case_variants(surface: str) -> list[str]:
    """Biến thể hoa/thường của tên Latin: "Taro YAMADA" cũng bắt "Yamada", "YAMADA". Không thêm
    chữ thường (tên trùng từ thường như "Will", "Bình" sẽ bị che khắp nơi)."""
    words = surface.split()
    title = " ".join(w[:1].upper() + w[1:].lower() for w in words)
    return [v for v in dict.fromkeys([surface, title, surface.upper()]) if v]


def _trie_pattern(words: Iterable[str]) -> str:
    """Regex dạng cây tiền tố: so khớp nhiều nghìn chuỗi nhanh hơn phép OR thường."""
    trie: dict[str, dict] = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = {}

    def build(node: dict[str, dict]) -> str:
        keys = sorted(k for k in node if k)
        if not keys:
            return ""
        alts = [re.escape(k) + build(node[k]) for k in keys]
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        if "" in node:
            return "(?:" + body + ")?"
        return body

    return build(trie)


class Masker:
    """Bảng nhãn của một lần hỏi. add_entities() trước, mask() bao nhiêu lần cũng được."""

    def __init__(self, ner: NerFn | None) -> None:
        self._ner = ner
        self._key_kind: dict[str, tuple[str, str]] = {}  # chuỗi chuẩn hóa → (loại, mã thực thể)
        self._surfaces: dict[str, str] = {}  # chuỗi NFKC để so khớp → chuỗi chuẩn hóa
        self._entity_label: dict[str, str] = {}  # mã thực thể → nhãn
        self._original: dict[str, str] = {}  # nhãn → giá trị gốc
        self._counter: Counter[str] = Counter()
        self._matchers: tuple[re.Pattern[str] | None, re.Pattern[str] | None] | None = None

    # --- bảng nhãn ---

    def _register(self, surface: str, kind: str, entity: str | None = None) -> str:
        """Ghi chuỗi vào từ điển; trả mã thực thể (chuỗi đã biết thì giữ thực thể cũ)."""
        key = _norm(surface)
        if not key:
            return ""
        known = self._key_kind.get(key)
        if known is None:
            known = (kind, entity or f"{kind}:{key}")
            self._key_kind[key] = known
            self._matchers = None
        form = _nfkc(surface)
        # Số ngắn (vd. "2026" bắt nhầm là tiền, "12" sau "Order No.") chỉ che tại chỗ, không lan:
        # nếu lan, mọi năm/ngày trong các nguồn khác bị che theo.
        short_number = _SHORT_NUMBER.fullmatch(form) and sum(c.isdigit() for c in form) < 6
        if len(form) >= 2 and form not in self._surfaces and not short_number:
            self._surfaces[form] = key
            self._matchers = None
            if _is_latin(form):
                for v in _case_variants(form)[1:]:
                    self._surfaces.setdefault(v, key)
            if known[0] == "ORG":
                core = company_core(form)
                if core:
                    self._register(core, "ORG", known[1])
        return known[1]

    def add_entities(self, entities: Iterable[Entity]) -> None:
        for ent in entities:
            entity_id = f"{ent.kind}:{_norm(ent.surfaces[0])}"
            for s in ent.surfaces:
                self._register(s, ent.kind, entity_id)
                if ent.kind == "PERSON" and _is_latin(s):
                    folded = fold_latin(s)
                    if folded != s:
                        self._register(folded, ent.kind, entity_id)

    def _label(self, surface: str, kind: str) -> str:
        entity = self._register(surface, kind)
        kind = self._key_kind[_norm(surface)][0]
        label = self._entity_label.get(entity)
        if label is None:
            self._counter[kind] += 1
            label = f"[{kind}_{self._counter[kind]}]"
            self._entity_label[entity] = label
            self._original[label] = surface.strip()
        elif len(surface.strip()) > len(self._original[label]):
            self._original[label] = surface.strip()  # thay ngược bằng dạng đầy đủ nhất
        return label

    @property
    def counts(self) -> dict[str, int]:
        return {k: self._counter[k] for k in KINDS if self._counter[k]}

    # --- so khớp từ điển (trên text đã NFKC) ---

    def _dictionary_spans(self, norm: str) -> list[Span]:
        if self._matchers is None:
            latin = [s for s in self._surfaces if _is_latin(s)]
            other = [s for s in self._surfaces if not _is_latin(s)]
            # Chuỗi Latin: phân biệt hoa thường (có biến thể Title/UPPER), theo ranh giới từ
            # Latin ("Bình" ≠ "bình thường"). Ranh giới chỉ xét chữ Latin/số: \w gồm cả chữ Nhật,
            # nên "Trầnです" sẽ không khớp nếu dùng \w.
            latin_re = (
                re.compile(
                    rf"(?<![{_LATIN_WORD}])" + _trie_pattern(latin) + rf"(?![{_LATIN_WORD}])"
                )
                if latin
                else None
            )
            other_re = re.compile(_trie_pattern(other)) if other else None
            self._matchers = (latin_re, other_re)
        spans: list[Span] = []
        for matcher in self._matchers:
            if matcher is None:
                continue
            for m in matcher.finditer(norm):
                key = self._surfaces.get(m.group(0))
                if key is not None:
                    spans.append(Span(m.start(), m.end(), self._key_kind[key][0]))
        return spans

    def _trim_to_known(self, text: str, span: Span) -> Span:
        """Tên bắt bằng kính ngữ có thể dính chữ đứng trước ("開発部田中様" → "発部田中"): nếu
        đuôi của khoảng là một tên đã biết thì thu lại đúng tên đó (giữ nhãn nhất quán)."""
        if span.kind != "PERSON":
            return span
        surface = text[span.start : span.end]
        for i in range(1, len(surface) - 1):
            key = self._surfaces.get(_nfkc(surface[i:]))
            if key is not None and self._key_kind[key][0] == "PERSON":
                return Span(span.start + i, span.end, span.kind)
        return span

    # --- che, thay ngược ---

    async def mask(self, texts: Sequence[str]) -> list[str]:
        """Che danh sách text (cùng bảng nhãn). NER lỗi → ném lỗi, không trả text chưa che.

        Phần regex và dựng từ điển (có thể vài nghìn tên) chạy ở thread riêng: không chặn event
        loop của API. Một Masker chỉ dùng trong một request, lần lượt: không cần khóa.
        """
        texts = [escape_labels(t) for t in texts]
        detected = await asyncio.to_thread(lambda: [self._patterns(t) for t in texts])
        if self._ner is not None and any(texts):
            found = await self._ner(list(texts))
            if len(found) != len(texts):
                raise ValueError("NER trả sai số lượng")
            for text, spans, ents in zip(texts, detected, found, strict=True):
                for e in ents:
                    cleaned = clean_ner_span(text, Span(e.start, e.end, e.label))
                    if cleaned is not None:
                        spans.append(cleaned)
        return await asyncio.to_thread(self._apply, texts, detected)

    @staticmethod
    def _to_original(spans: Iterable[Span], starts: list[int], ends: list[int]) -> list[Span]:
        return [Span(starts[s.start], ends[s.end - 1], s.kind) for s in spans if s.end > s.start]

    def _patterns(self, text: str) -> list[Span]:
        norm, starts, ends = nfkc_map(text)
        return self._to_original(pattern_spans(norm), starts, ends)

    def _apply(self, texts: list[str], detected: list[list[Span]]) -> list[str]:
        detected = [
            [self._trim_to_known(t, s) for s in spans]
            for t, spans in zip(texts, detected, strict=True)
        ]
        # Pha 1: ghi mọi chuỗi phát hiện được vào từ điển (để lan sang text khác).
        for text, spans in zip(texts, detected, strict=True):
            for s in resolve(spans):
                self._register(text[s.start : s.end], s.kind)
        # Pha 2: che từng text bằng khoảng phát hiện + mọi chuỗi đã biết.
        out: list[str] = []
        for text, spans in zip(texts, detected, strict=True):
            norm, starts, ends = nfkc_map(text)
            known = self._to_original(self._dictionary_spans(norm), starts, ends)
            chosen = resolve([*spans, *known])
            parts: list[str] = []
            pos = 0
            for s in chosen:
                surface = text[s.start : s.end]
                if not surface.strip():
                    continue
                # Giữ khoảng trắng đầu/cuối của khoảng bên ngoài nhãn.
                lead = len(surface) - len(surface.lstrip())
                trail = len(surface) - len(surface.rstrip())
                parts.append(text[pos : s.start + lead])
                parts.append(self._label(surface, s.kind))
                pos = s.end - trail
            parts.append(text[pos:])
            out.append("".join(parts))
        return out

    def unmask(self, text: str) -> str:
        return LABEL_RE.sub(lambda m: self._original.get(m.group(0), m.group(0)), text)
