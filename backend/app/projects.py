"""Khớp mail với bộ lọc dự án (spec 2026-10-31-project-settings-design.md mục 3).

Chỉ có hàm thuần, không đụng DB. So khớp bằng toán tử `in` sau NFKC + casefold, không regex.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


def normalize(value: str) -> str:
    """NFKC rồi casefold, dùng cho cả giá trị bộ lọc lẫn giá trị mail."""
    return unicodedata.normalize("NFKC", value).casefold()


def addresses(value: Any) -> tuple[str, ...]:
    """Lấy các `addr` (chuỗi) từ JSONB địa chỉ. Bỏ phần tử sai dạng, không bao giờ ném lỗi."""
    if not isinstance(value, list):
        return ()
    found: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        addr = item.get("addr")
        if isinstance(addr, str):
            found.append(addr)
    return tuple(found)


@dataclass(frozen=True)
class MailFields:
    """Các trường của mail dùng để khớp, giá trị thô (chưa chuẩn hóa)."""

    from_addr: str | None
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str | None


@dataclass(frozen=True)
class ProjectRule:
    """Bộ lọc của một dự án; giá trị đã `normalize`."""

    id: int
    from_: tuple[str, ...]
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: tuple[str, ...]


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    return any(n in text for n in needles)


def match_projects(mail: MailFields, rules: Sequence[ProjectRule]) -> set[int]:
    """Trả về id các dự án khớp mail. Dự án trống cả 4 ô không nhận mail nào."""
    # Chuẩn hóa giá trị mail một lần cho mỗi mail.
    from_addr = normalize(mail.from_addr) if mail.from_addr else ""
    to = [normalize(a) for a in mail.to]
    cc = [normalize(a) for a in mail.cc]
    subject = normalize(mail.subject) if mail.subject else ""

    matched: set[int] = set()
    for rule in rules:
        if not (rule.from_ or rule.to or rule.cc or rule.subject):
            continue
        if rule.from_ or rule.to or rule.cc:
            address_ok = (
                _contains_any(from_addr, rule.from_)
                or any(_contains_any(a, rule.to) for a in to)
                or any(_contains_any(a, rule.cc) for a in cc)
            )
        else:
            address_ok = True
        subject_ok = not rule.subject or _contains_any(subject, rule.subject)
        if address_ok and subject_ok:
            matched.add(rule.id)
    return matched
