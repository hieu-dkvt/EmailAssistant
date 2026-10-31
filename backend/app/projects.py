"""Khớp mail với bộ lọc dự án (spec 2026-10-31-project-settings-design.md mục 3).

Chỉ có hàm thuần, không đụng DB. So khớp bằng toán tử `in` sau NFKC + casefold, không regex.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web_schemas import Project, ProjectInput, ProjectList


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


# --- CRUD cho API web (spec mục 6). Chạy trong user_session (RLS); vẫn lọc user_id tường minh. ---


class ProjectError(Exception):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.detail = detail


MAX_PROJECTS = 100
FILTER_COLUMNS = ("filter_from", "filter_to", "filter_cc", "filter_subject")
LIST_COLUMNS = (*FILTER_COLUMNS, "github_mail", "backlog_mail")
DATA_COLUMNS = (
    "name",
    *FILTER_COLUMNS,
    "docs_repo_url",
    "docs_local_path",
    "github_mail",
    "review_repo_url",
    "review_local_path",
    "backlog_mail",
    "backlog_space_url",
    "backlog_project_key",
    "redmine_url",
    "redmine_project",
)
ALL_COLUMNS = ("id", *DATA_COLUMNS, "created_at", "updated_at")


def _value(col: str) -> str:
    return f"CAST(:{col} AS text[])" if col in LIST_COLUMNS else f":{col}"


_SELECT = ", ".join(ALL_COLUMNS)
_SET = ", ".join(f"{c} = {_value(c)}" for c in DATA_COLUMNS)
LIST = text(f"SELECT {_SELECT} FROM projects WHERE user_id = :u ORDER BY name, id")
COUNT = text("SELECT count(*) FROM projects WHERE user_id = :u")
ASSIGNING = text(
    "SELECT rules_version > applied_rules_version FROM user_project_state WHERE user_id = :u"
)
INSERT = text(
    f"INSERT INTO projects (user_id, {', '.join(DATA_COLUMNS)}) "
    f"VALUES (:u, {', '.join(_value(c) for c in DATA_COLUMNS)}) RETURNING {_SELECT}"
)
OLD_FILTERS = text(
    f"SELECT {', '.join(FILTER_COLUMNS)} FROM projects WHERE id = :id AND user_id = :u FOR UPDATE"
)
UPDATE = text(
    f"UPDATE projects SET {_SET}, updated_at = now() "
    f"WHERE id = :id AND user_id = :u RETURNING {_SELECT}"
)
DELETE = text("DELETE FROM projects WHERE id = :id AND user_id = :u RETURNING id")
# Tăng version trong cùng transaction với thay đổi bộ lọc: worker thấy version mới mới quét lại.
BUMP_RULES = text(
    """
    INSERT INTO user_project_state (user_id, rules_version) VALUES (:u, 1)
    ON CONFLICT (user_id) DO UPDATE
    SET rules_version = user_project_state.rules_version + 1, updated_at = now()
    """
)


def _name_taken(e: IntegrityError) -> ProjectError | None:
    """Trùng (user_id, name) do hai request lưu cùng lúc: đổi thành 409. Lỗi khác: None."""
    if getattr(e.orig, "sqlstate", None) == "23505":
        return ProjectError(409, "project_name_taken", "Đã có dự án trùng tên")
    return None


def _params(user_id: int, req: ProjectInput) -> dict[str, Any]:
    return {"u": user_id, **req.model_dump()}


async def list_projects(s: AsyncSession, user_id: int) -> ProjectList:
    rows = await s.execute(LIST, {"u": user_id})
    items = [Project(**r._mapping) for r in rows]
    assigning = bool(await s.scalar(ASSIGNING, {"u": user_id}))
    return ProjectList(items=items, assigning=assigning)


async def create_project(s: AsyncSession, user_id: int, req: ProjectInput) -> Project:
    if (await s.scalar(COUNT, {"u": user_id}) or 0) >= MAX_PROJECTS:
        raise ProjectError(409, "project_limit", f"Tối đa {MAX_PROJECTS} dự án")
    try:
        row = (await s.execute(INSERT, _params(user_id, req))).one()
    except IntegrityError as e:
        taken = _name_taken(e)
        if taken is None:
            raise
        raise taken from None
    if any(getattr(req, c) for c in FILTER_COLUMNS):
        await s.execute(BUMP_RULES, {"u": user_id})
    return Project(**row._mapping)


async def update_project(
    s: AsyncSession, user_id: int, project_id: int, req: ProjectInput
) -> Project:
    old = (await s.execute(OLD_FILTERS, {"id": project_id, "u": user_id})).first()
    if old is None:
        raise ProjectError(404, "project_not_found", "Không có dự án này")
    try:
        row = (await s.execute(UPDATE, {**_params(user_id, req), "id": project_id})).one()
    except IntegrityError as e:
        taken = _name_taken(e)
        if taken is None:
            raise
        raise taken from None
    if any(list(old._mapping[c]) != getattr(req, c) for c in FILTER_COLUMNS):
        await s.execute(BUMP_RULES, {"u": user_id})
    return Project(**row._mapping)


async def delete_project(s: AsyncSession, user_id: int, project_id: int) -> None:
    if await s.scalar(DELETE, {"id": project_id, "u": user_id}) is None:
        raise ProjectError(404, "project_not_found", "Không có dự án này")
