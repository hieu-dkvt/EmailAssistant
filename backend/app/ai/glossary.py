"""Glossary: so khớp thuật ngữ trong text đã che và nạp mục theo mail (spec 26 mục 9).

`match_glossary` là hàm thuần: NFKC theo cụm bằng `nfkc_map` (katakana nửa độ rộng, dạng NFD cần
gộp ký tự với dấu theo sau), rồi casefold, bỏ khoảng trắng, vẫn giữ được vị trí; tìm bằng
`str.find`, không regex dựng từ dữ liệu người dùng. Không log thuật ngữ hay bản dịch.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.masking import LABEL_RE, nfkc_map

MAX_GLOSSARY_IN_PROMPT = 60
MAX_TERMS_PER_SCOPE = 5_000
MAX_OCCURRENCES_PER_TERM = 100  # chặn thuật ngữ ngắn lặp dày trên text dài
_LATIN_EXTRA = "ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúýĂăĐđĨĩŨũƠơƯư" + "".join(
    chr(c) for c in range(0x1EA0, 0x1EFA)
)


def normalize_term(value: str) -> str:
    """NFKC, casefold, bỏ mọi khoảng trắng."""
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _is_latin_char(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in _LATIN_EXTRA


def _is_latin_word(value: str) -> bool:
    return bool(value) and all(_is_latin_char(c) for c in value)


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    term_norm: str
    translation: str
    note: str | None
    project_id: int | None


@dataclass(frozen=True)
class GlossaryMatch:
    term: str
    term_norm: str
    translations: tuple[str, ...]
    note: str | None
    conflict: bool


def _fold_chars(nfkc: str) -> tuple[str, list[int]]:
    """Bản NFKC đã casefold, bỏ khoảng trắng, và vị trí từng ký tự của nó trong bản NFKC."""
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(nfkc):
        for p in "".join(ch.casefold().split()):
            chars.append(p)
            origin.append(i)
    return "".join(chars), origin


def merge_entries(entries: Sequence[GlossaryEntry]) -> dict[str, GlossaryMatch]:
    """Gộp theo term_norm: mục dự án thắng mục chung; nhiều bản dịch khác nhau là xung đột."""
    groups: dict[str, list[GlossaryEntry]] = {}
    for e in entries:
        if e.term_norm:
            groups.setdefault(e.term_norm, []).append(e)
    merged: dict[str, GlossaryMatch] = {}
    for norm, group in groups.items():
        scoped = [e for e in group if e.project_id is not None]
        chosen = scoped or group
        translations = tuple(dict.fromkeys(e.translation for e in chosen))
        note = next((e.note for e in chosen if e.note), None)
        merged[norm] = GlossaryMatch(
            term=chosen[0].term,
            term_norm=norm,
            translations=translations,
            note=note,
            conflict=len(translations) > 1,
        )
    return merged


def match_glossary(
    text: str, entries: Sequence[GlossaryEntry], limit: int = MAX_GLOSSARY_IN_PROMPT
) -> list[GlossaryMatch]:
    """Các mục glossary xuất hiện trong text; cụm dài trước, không chồng lấn, tối đa `limit`."""
    merged = merge_entries(entries)
    if not merged or not text or limit <= 0:
        return []
    nfkc, starts, _ = nfkc_map(text)
    norm, origin = _fold_chars(nfkc)
    blocked = bytearray(len(norm))  # 1 = ký tự nằm trong nhãn che, không khớp được
    labelled = [False] * len(text)
    for m in LABEL_RE.finditer(text):
        for i in range(m.start(), m.end()):
            labelled[i] = True
    for j, o in enumerate(origin):
        if labelled[starts[o]]:
            blocked[j] = 1

    found: list[tuple[int, int, str]] = []  # (độ dài, vị trí, term_norm)
    for term_norm, match in merged.items():
        latin = _is_latin_word(term_norm)
        size = len(term_norm)
        start = norm.find(term_norm)
        count = 0
        while start != -1 and count < MAX_OCCURRENCES_PER_TERM:
            end = start + size
            if not any(blocked[start:end]) and (
                not latin or _word_bounded(nfkc, origin, start, end)
            ):
                found.append((size, start, match.term_norm))
                count += 1
            start = norm.find(term_norm, start + 1)

    found.sort(key=lambda f: (-f[0], f[1]))
    covered = bytearray(len(norm))
    accepted: dict[str, int] = {}  # term_norm -> độ dài (thứ tự nhận = dài trước)
    for size, start, term_norm in found:
        if any(covered[start : start + size]):
            continue
        covered[start : start + size] = b"\x01" * size
        accepted.setdefault(term_norm, size)
    ordered = sorted(accepted, key=lambda n: (-accepted[n], n))
    return [merged[n] for n in ordered[:limit]]


def _word_bounded(nfkc: str, origin: list[int], start: int, end: int) -> bool:
    """Xét trên bản NFKC: ở text NFD, ký tự ngay trước thuật ngữ có thể chỉ là dấu tổ hợp."""
    before = origin[start] - 1
    after = origin[end - 1] + 1
    if before >= 0 and _is_latin_char(nfkc[before]):
        return False
    return not (after < len(nfkc) and _is_latin_char(nfkc[after]))


_LOAD = text(
    """
    SELECT term, term_norm, translation, note, project_id
    FROM glossary_terms
    WHERE user_id = :u
      AND (project_id IS NULL
           OR project_id IN (SELECT project_id FROM message_projects
                             WHERE user_id = :u AND message_id = :m))
    ORDER BY id
    """
)


async def load_entries(session: AsyncSession, user_id: int, message_id: int) -> list[GlossaryEntry]:
    """Mục chung và mục của các dự án chứa mail. Chạy trong user_session (RLS)."""
    rows = await session.execute(_LOAD, {"u": user_id, "m": message_id})
    return [
        GlossaryEntry(
            term=r.term,
            term_norm=r.term_norm,
            translation=r.translation,
            note=r.note,
            project_id=r.project_id,
        )
        for r in rows
    ]
