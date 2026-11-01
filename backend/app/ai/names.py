"""Tách tên hiển thị trong header (From/To/Cc) thành thực thể để che (spec 20 mục 4.2, 4.3).

Hàm thuần, không đọc DB. Mẫu tên công ty dùng chung cho việc che trong nội dung (masking.py).
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

# Ranh giới từ cho giá trị Latin: không dùng \w hay \b. \w gồm cả kana, kanji, mà tiếng Nhật viết
# liền không có khoảng trắng: "連絡先はtanaka@…まで" sẽ không khớp mẫu và lọt ra ngoài chưa che.
LATIN_WORD = "A-Za-z0-9À-ỹĐđ"
WORD_CHARS = LATIN_WORD + "_"
NOT_AFTER_WORD = rf"(?<![{WORD_CHARS}])"
NOT_BEFORE_WORD = rf"(?![{WORD_CHARS}])"

# Chữ dùng trong tên công ty Nhật: Hán, katakana, Latin, số (full/half width), vài ký hiệu.
# Dừng ở hiragana (trợ từ の, は…), dấu câu, khoảng trắng.
_JA_NAME_CHARS = r"[A-Za-z0-9Ａ-Ｚａ-ｚ０-９㐀-䶿一-鿿々ァ-ヶー・＆&.\-]"
_JA_CORP = (
    r"(?:株式会社|有限会社|合同会社|合資会社|合名会社|(?:一般|公益)(?:社団|財団)法人|独立行政法人)"
)
_JA_CORP_ABBR = r"(?:\(株\)|（株）|㈱|\(有\)|（有）|㈲)"
_EN_CORP_SUFFIX = (
    r"(?:Co\.,?\s?Ltd\.?|Co\.|Inc\.?|Corp\.?|Corporation|LLC|Ltd\.?|JSC|K\.K\.|GmbH"
    r"|Pte\.?\s?Ltd\.?)"
)

# Tên bắt đầu bằng chữ hoặc số (không bằng ký hiệu: "--\n株式会社" không phải tên).
_JA_NAME = rf"[A-Za-z0-9Ａ-Ｚａ-ｚ０-９㐀-䶿一-鿿々ァ-ヶ]{_JA_NAME_CHARS}{{0,19}}"

COMPANY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Không nhận khoảng trắng sau 株式会社: "サンプルB株式会社 営業企画部" là tên + phòng ban.
    re.compile(rf"{_JA_CORP}{_JA_NAME}"),
    re.compile(rf"{_JA_NAME}[ 　]?{_JA_CORP}"),
    re.compile(rf"{_JA_CORP_ABBR}[ 　]?{_JA_NAME}"),
    re.compile(rf"{_JA_NAME}{_JA_CORP_ABBR}"),
    # Tên viết hoa từng từ + hậu tố công ty tiếng Anh: "Vendor C Inc.", "ABC Co., Ltd."
    # Mọi lượng từ có giới hạn: chạy tuyến tính trên nội dung mail (vd. chuỗi "A-A-A-…" dài).
    re.compile(
        rf"{NOT_AFTER_WORD}(?:[A-Z][\w&\-]{{0,30}}\.?[ \t]){{1,4}}{_EN_CORP_SUFFIX}"
        rf"{NOT_BEFORE_WORD}"
    ),
    # Tiếng Việt: "Công ty TNHH ABC", "Cty CP Phần mềm XYZ" (các từ viết hoa sau đó).
    re.compile(
        rf"{NOT_AFTER_WORD}(?:Công ty|CÔNG TY|Công Ty|Cty|CTY)[ \t]{{1,3}}"
        r"(?:(?:TNHH|CP|Cổ phần|cổ phần|CỔ PHẦN)[ \t]{1,3})?"
        r"[A-ZĐÀ-Ỹ0-9][A-Za-zÀ-ỹĐđ0-9&\-]{0,30}(?:[ \t][A-ZĐÀ-Ỹ0-9][A-Za-zÀ-ỹĐđ0-9&\-]{0,30}){0,5}"
    ),
)
# "…御中" (gửi công ty/phòng ban): phần trước 御中 là tổ chức.
ONCHU = re.compile(rf"({_JA_NAME_CHARS}{{2,20}})\s?御中")

# Phòng ban, chức danh, từ chung: không phải tên người.
_DEPT_SUFFIXES = (
    "部",
    "課",
    "室",
    "係",
    "チーム",
    "グループ",
    "センター",
    "本部",
    "支店",
    "営業所",
    "事業部",
)
_GENERIC = {
    "担当",
    "担当者",
    "事務局",
    "窓口",
    "受付",
    "サポート",
    "お問い合わせ",
    "問い合わせ",
    "システム",
    "営業",
    "総務",
    "経理",
    "人事",
    "広報",
    "管理者",
    "代表",
    "各位",
    "御中",
}
_GENERIC_LATIN = {
    "team",
    "dept",
    "department",
    "division",
    "group",
    "support",
    "sales",
    "info",
    "admin",
    "noreply",
    "no-reply",
    "notification",
    "notifications",
    "system",
    "office",
    "hr",
    "accounting",
    "contact",
    "service",
    "services",
    "mailer-daemon",
    "postmaster",
    "help",
    "helpdesk",
    "news",
    "newsletter",
    "marketing",
    "billing",
    "the",
    "and",
    "for",
    "from",
}
_SEPARATORS = re.compile(r"\s[-–]\s|[/|,、()（）\[\]【】「」<>＜＞／｜]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿々]")
_VI_LETTER = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.I)


@dataclass(frozen=True)
class Entity:
    kind: str  # PERSON | ORG | EMAIL
    surfaces: tuple[str, ...]  # surfaces[0] là dạng đầy đủ (dùng khi thay ngược)


def fold_latin(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'Trần Hiếu' → 'Tran Hieu' (mail tiếng Anh thường viết không dấu)."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize(
        "NFC", "".join(c for c in decomposed if not unicodedata.combining(c))
    )


_CORP_AFFIX = re.compile(
    rf"^(?:{_JA_CORP}|{_JA_CORP_ABBR})\s?|\s?(?:{_JA_CORP}|{_JA_CORP_ABBR})$"
    rf"|,?\s{_EN_CORP_SUFFIX}$"
    r"|^(?:Công ty|CÔNG TY|Công Ty|Cty|CTY)\s+(?:(?:TNHH|CP|Cổ phần|cổ phần|CỔ PHẦN)\s+)?"
)


def company_core(name: str) -> str | None:
    """Tên công ty bỏ loại hình: '株式会社サンプルA' → 'サンプルA' (hay viết tắt trong subject
    【サンプルA】). Ngắn hơn 3 ký tự → None (dễ trùng từ thường)."""
    core = _CORP_AFFIX.sub("", name.strip()).strip(" ・,")
    return core if core != name.strip() and len(core) >= 3 else None


def find_companies(text: str) -> list[tuple[int, int]]:
    """Vị trí tên công ty theo mẫu (có thể chồng nhau; bên gọi tự gộp)."""
    spans = [(m.start(), m.end()) for p in COMPANY_PATTERNS for m in p.finditer(text)]
    spans += [(m.start(1), m.end(1)) for m in ONCHU.finditer(text)]
    return [(s, e) for s, e in spans if e > s]


def companies_in(texts: Iterable[str]) -> list[Entity]:
    """Công ty nhắc tới trong text (vd. chữ ký mail, đã bị cắt khỏi body_clean nhưng tên viết tắt
    vẫn có trong subject 【サンプルA】): mỗi tên một thực thể ORG kèm tên lõi."""
    seen: dict[str, Entity] = {}
    for t in texts:
        for s, e in leftmost(find_companies(t)):
            name = t[s:e].strip()
            if len(name) >= 3 and name not in seen:
                seen[name] = Entity("ORG", _unique([name, company_core(name) or ""]))
    return list(seen.values())


def leftmost(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Chọn các khoảng không chồng nhau: bắt đầu sớm nhất trước, cùng điểm đầu thì dài hơn."""
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans, key=lambda x: (x[0], x[0] - x[1])):
        if not out or s >= out[-1][1]:
            out.append((s, e))
    return out


def _is_generic(token: str) -> bool:
    t = token.strip(" .・")
    if not t:
        return True
    if t.lower() in _GENERIC_LATIN or t in _GENERIC:
        return True
    return _CJK.search(t) is not None and t.endswith(_DEPT_SUFFIXES)


def _person_surfaces(tokens: list[str]) -> list[str]:
    full = " ".join(tokens)
    out = [full]
    if len(tokens) > 1:
        out.append("".join(tokens))
    if _CJK.search(full):
        # Tên Nhật "田中 健二": thêm họ và tên riêng (mỗi phần ≥ 2 ký tự).
        if len(tokens) > 1:
            out += [t for t in tokens if len(t) >= 2]
        return out
    vietnamese = _VI_LETTER.search(full) is not None
    if len(tokens) > 1:
        if vietnamese:
            # Họ và tên gọi (bỏ tên đệm Văn, Thị…). Tên gọi tiếng Việt ngắn (An, Hà) vẫn che:
            # so khớp phân biệt hoa thường theo ranh giới từ (masking.py).
            out += [t for t in (tokens[0], tokens[-1]) if len(t) >= 2]
        else:
            out += [t for t in tokens if len(t) >= 3]
    if vietnamese:
        out += [fold_latin(s) for s in list(out)]
    return out


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for s in items:
        s = s.strip()
        if len(s) >= 2 and s not in seen:
            seen.append(s)
    return tuple(seen)


def entities_from_display(name: str | None, addr: str | None) -> list[Entity]:
    """Tên hiển thị + địa chỉ mail → thực thể (người, công ty, email)."""
    out: list[Entity] = []
    if addr and "@" in addr:
        out.append(Entity("EMAIL", (addr.strip(),)))
    if not name:
        return out
    name = unicodedata.normalize("NFKC", name).replace("　", " ").strip().strip("\"'")
    if not name or "@" in name and " " not in name:
        return out
    for segment in _SEPARATORS.split(name):
        segment = segment.strip()
        if not segment:
            continue
        rest = segment
        for s, e in leftmost(find_companies(segment)):
            company = segment[s:e].strip()
            if company and company in rest:
                out.append(Entity("ORG", _unique([company, company_core(company) or ""])))
                rest = rest.replace(company, " ")
        tokens = [t for t in rest.split() if not _is_generic(t)]
        if not tokens or len(tokens) > 5:
            continue
        surfaces = _unique(_person_surfaces(tokens))
        if surfaces:
            out.append(Entity("PERSON", surfaces))
    return out
