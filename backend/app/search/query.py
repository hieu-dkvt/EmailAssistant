"""Dựng truy vấn Groonga từ chuỗi người dùng gõ (spec 14 mục 4).

Không đưa thẳng chuỗi của người dùng cho Groonga: mỗi từ được bọc trong "…", nên không có lỗi cú
pháp và không dùng được cú pháp lạ (column:value, *, …). Hỗ trợ: AND (khoảng trắng), OR (viết
hoa, giữa hai từ), -từ (phủ định), "cụm từ".
"""

import unicodedata
from dataclasses import dataclass

MAX_QUERY_CHARS = 500
MAX_TERMS = 20


class QueryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedQuery:
    groonga: str
    keywords: tuple[str, ...]  # từ không phủ định, để làm nổi bật


@dataclass(frozen=True)
class _Token:
    text: str
    negated: bool = False
    is_or: bool = False


def _lex(q: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(q)
    while i < n:
        if q[i].isspace():
            i += 1
            continue
        negated = q[i] == "-" and i + 1 < n and not q[i + 1].isspace()
        if negated:
            i += 1
        if q[i] == '"':
            end = q.find('"', i + 1)
            end = n if end == -1 else end  # thiếu dấu đóng: cụm kéo tới cuối
            text, quoted = q[i + 1 : end], True
            i = end + 1
        else:
            start = i
            while i < n and not q[i].isspace():
                i += 1
            text, quoted = q[start:i], False
        if not text.strip():
            continue
        if text == "-" and not quoted:
            continue
        tokens.append(_Token(text, negated, is_or=(text == "OR" and not quoted and not negated)))
    return tokens


def _quote(term: str) -> str:
    return '"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_query(raw: str | None, *, any_term: bool = False) -> ParsedQuery | None:
    """None: không có từ nào (chuỗi rỗng). Lỗi người dùng sửa được → QueryError.

    any_term: mail chỉ cần có một trong các từ không phủ định (nhánh từ khóa của tìm kiếm hybrid,
    spec 19 mục 4); từ phủ định vẫn loại trừ.
    """
    q = unicodedata.normalize("NFKC", (raw or "").replace("\x00", "")).strip()
    if len(q) > MAX_QUERY_CHARS:
        raise QueryError("query_too_long", f"Chuỗi tìm kiếm dài quá {MAX_QUERY_CHARS} ký tự")
    tokens = _lex(q)
    positives: list[_Token] = []
    negatives = [t for t in tokens if t.negated]
    for i, t in enumerate(tokens):
        if t.negated:
            continue
        if t.is_or:
            # OR chỉ có nghĩa khi đứng giữa hai từ không phủ định.
            prev_ok = bool(positives) and not positives[-1].is_or
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if not (prev_ok and nxt is not None and not nxt.negated and not nxt.is_or):
                continue
        positives.append(t)
    terms = [t for t in positives if not t.is_or]
    if not terms:
        if negatives:
            raise QueryError(
                "query_needs_positive_term",
                "Cần ít nhất một từ không có dấu - phía trước",
            )
        return None
    if len(terms) + len(negatives) > MAX_TERMS:
        raise QueryError("query_too_long", f"Tối đa {MAX_TERMS} từ khóa")
    if any_term:
        parts = ["(" + " OR ".join(_quote(t.text) for t in terms) + ")"]
    else:
        parts = ["OR" if t.is_or else _quote(t.text) for t in positives]
    parts += ["-" + _quote(t.text) for t in negatives]
    keywords: list[str] = []
    seen: set[str] = set()
    for t in terms:
        key = t.text.lower()
        if key not in seen:
            seen.add(key)
            keywords.append(t.text)
    return ParsedQuery(" ".join(parts), tuple(keywords))
