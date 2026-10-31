"""Khớp mail với bộ lọc dự án, CRUD cho API web và việc gán của worker
(spec 2026-10-31-project-settings-design.md mục 3, 5, 6).

Phần khớp là hàm thuần: so khớp bằng toán tử `in` sau NFKC + casefold, không regex.
"""

import logging
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.locks import USER_MAIL_LOCK
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


# --- Worker gán mail vào dự án (spec mục 5). Role app_worker (BYPASSRLS): lọc theo user_id ---

log = logging.getLogger("app.projects")

ASSIGN_BATCH = 2_000
ASSIGN_WRITE_ATTEMPTS = 3  # số lần ghi một lô khi dự án / mail đang bị xóa
RETRY_SQLSTATES = frozenset({"23503", "40P01"})  # foreign_key_violation, deadlock_detected
ASSIGN_JOB_SECONDS = 10.0  # một job assign_user_projects chạy tối đa ngần này rồi nhường worker

LOAD_RULES = text(
    "SELECT id, filter_from, filter_to, filter_cc, filter_subject FROM projects "
    "WHERE user_id = :u ORDER BY id"
)
SELECT_MATCH_FIELDS = text(
    "SELECT id, from_addr, to_addrs, cc_addrs, subject FROM messages "
    "WHERE user_id = :u AND id = ANY(CAST(:ids AS bigint[])) ORDER BY id"
)
DELETE_LINKS = text(
    "DELETE FROM message_projects WHERE user_id = :u AND message_id = ANY(CAST(:ids AS bigint[]))"
)
# JOIN projects / messages: dự án hoặc mail vừa bị xóa sau khi đọc thì bỏ qua, không lỗi FK.
INSERT_LINKS = text(
    """
    INSERT INTO message_projects (user_id, message_id, project_id)
    SELECT :u, c.message_id, c.project_id
    FROM unnest(CAST(:mids AS bigint[]), CAST(:pids AS bigint[])) AS c(message_id, project_id)
    JOIN projects p ON p.id = c.project_id AND p.user_id = :u
    JOIN messages m ON m.id = c.message_id AND m.user_id = :u
    ON CONFLICT DO NOTHING
    """
)
SELECT_STATE = text(
    "SELECT rules_version, applied_rules_version, scan_version, scan_after_id "
    "FROM user_project_state WHERE user_id = :u"
)
NEXT_SCAN_IDS = text(
    "SELECT id FROM messages WHERE user_id = :u AND id > :after ORDER BY id LIMIT :n"
)
START_SCAN = text(
    "UPDATE user_project_state SET scan_version = :v, scan_after_id = 0, updated_at = now() "
    "WHERE user_id = :u"
)
ADVANCE_SCAN = text(
    "UPDATE user_project_state SET scan_after_id = :after, updated_at = now() WHERE user_id = :u"
)
FINISH_SCAN = text(
    "UPDATE user_project_state SET scan_after_id = :after, applied_rules_version = :v, "
    "updated_at = now() WHERE user_id = :u"
)
PENDING_RULE_USERS = text(
    "SELECT user_id FROM user_project_state "
    "WHERE rules_version > applied_rules_version ORDER BY user_id"
)
# Chỉ ghi các cột app_worker được UPDATE: scan_version khác mọi version thật nên lần sau quét lại
# từ đầu; applied < rules để pending_rule_users thấy.
RESET_SCAN = text(
    "UPDATE user_project_state SET scan_version = -1, applied_rules_version = rules_version - 1, "
    "updated_at = now() WHERE user_id = :u"
)


@dataclass
class AssignStats:
    messages: int = 0  # số mail đã xét
    more: bool = False  # còn mail chưa quét (job tự đẩy tiếp)


async def load_rules(s: AsyncSession, user_id: int) -> list[ProjectRule]:
    """Bộ lọc hiện hành. Giá trị rỗng sau chuẩn hóa bị bỏ: `"" in text` khớp mọi mail."""

    def clean(values: Sequence[str] | None) -> tuple[str, ...]:
        return tuple(v for v in (normalize(x) for x in values or ()) if v)

    rows = await s.execute(LOAD_RULES, {"u": user_id})
    return [
        ProjectRule(
            id=r.id,
            from_=clean(r.filter_from),
            to=clean(r.filter_to),
            cc=clean(r.filter_cc),
            subject=clean(r.filter_subject),
        )
        for r in rows
    ]


async def assign_messages(
    s: AsyncSession, user_id: int, message_ids: Sequence[int], rules: Sequence[ProjectRule]
) -> int:
    """Gán lại dự án cho các mail: xóa liên kết cũ rồi ghi theo `rules`. Trả số mail có dự án."""
    with_project = 0
    for i in range(0, len(message_ids), ASSIGN_BATCH):
        chunk = list(message_ids[i : i + ASSIGN_BATCH])
        rows = (await s.execute(SELECT_MATCH_FIELDS, {"u": user_id, "ids": chunk})).all()
        mids: list[int] = []
        pids: list[int] = []
        for r in rows:
            mail = MailFields(
                from_addr=r.from_addr,
                to=addresses(r.to_addrs),
                cc=addresses(r.cc_addrs),
                subject=r.subject,
            )
            matched = match_projects(mail, rules)
            if matched:
                with_project += 1
            for pid in sorted(matched):
                mids.append(r.id)
                pids.append(pid)
        await _write_links(s, user_id, [r.id for r in rows], mids, pids)
    return with_project


async def _write_links(
    s: AsyncSession, user_id: int, ids: list[int], mids: list[int], pids: list[int]
) -> None:
    """Xóa rồi ghi liên kết của một lô, trong SAVEPOINT; thử lại khi dự án / mail đang bị xóa.

    Xóa chưa commit của người khác vẫn lọt qua JOIN trong INSERT_LINKS; FK chờ rồi báo 23503
    (hoặc 40P01 khi hai bên chờ nhau). Sau khi xóa commit, lần thử lại thấy dòng đã mất và bỏ qua.
    """
    for attempt in range(1, ASSIGN_WRITE_ATTEMPTS + 1):
        try:
            async with s.begin_nested():
                await s.execute(DELETE_LINKS, {"u": user_id, "ids": ids})
                if mids:
                    await s.execute(INSERT_LINKS, {"u": user_id, "mids": mids, "pids": pids})
            return
        except DBAPIError as e:
            sqlstate = getattr(e.orig, "sqlstate", None)
            if sqlstate not in RETRY_SQLSTATES or attempt == ASSIGN_WRITE_ATTEMPTS:
                raise
            log.warning("assign user=%s retry sqlstate=%s attempt=%d", user_id, sqlstate, attempt)


async def assign_batch(engine: AsyncEngine, user_id: int, batch: int = ASSIGN_BATCH) -> AssignStats:
    """Quét một lô mail theo bộ lọc hiện hành, một transaction (spec mục 5, 5 bước)."""
    async with AsyncSession(engine) as s, s.begin():
        await s.execute(USER_MAIL_LOCK, {"u": str(user_id)})
        state = (await s.execute(SELECT_STATE, {"u": user_id})).first()
        if state is None or state.applied_rules_version >= state.rules_version:
            return AssignStats()
        # Đọc version TRƯỚC khi đọc projects: sửa bộ lọc commit sau đó làm version lớn hơn v,
        # lần sau quét lại.
        v = state.rules_version
        rules = await load_rules(s, user_id)
        after = state.scan_after_id
        if state.scan_version != v:
            await s.execute(START_SCAN, {"u": user_id, "v": v})
            after = 0
        ids = list(await s.scalars(NEXT_SCAN_IDS, {"u": user_id, "after": after, "n": batch}))
        if ids:
            await assign_messages(s, user_id, ids, rules)
            after = ids[-1]
        done = len(ids) < batch
        await s.execute(
            FINISH_SCAN if done else ADVANCE_SCAN, {"u": user_id, "after": after, "v": v}
        )
    return AssignStats(messages=len(ids), more=not done)


async def assign_user(
    engine: AsyncEngine,
    user_id: int,
    *,
    budget_seconds: float = ASSIGN_JOB_SECONDS,
    batch: int = ASSIGN_BATCH,
) -> AssignStats:
    """Quét theo lô tới khi xong hoặc hết thời gian; more = còn mail chưa quét."""
    started = time.monotonic()
    total = AssignStats()
    while True:
        stats = await assign_batch(engine, user_id, batch)
        total.messages += stats.messages
        total.more = stats.more
        if not stats.more or time.monotonic() - started >= budget_seconds:
            break
    if total.messages:
        log.info(
            "assign user=%s messages=%d more=%s ms=%d",
            user_id,
            total.messages,
            total.more,
            (time.monotonic() - started) * 1000,
        )
    return total


async def pending_rule_users(engine: AsyncEngine) -> list[int]:
    """Người dùng có bộ lọc chưa áp dụng. Chỉ đọc bảng nhỏ user_project_state (chạy mỗi 5 giây)."""
    async with AsyncSession(engine) as s:
        return list(await s.scalars(PENDING_RULE_USERS))


async def reset_scan(engine: AsyncEngine, user_id: int) -> bool:
    """Bắt quét lại từ đầu. False khi người dùng chưa có dòng state (chưa có bộ lọc nào)."""
    async with AsyncSession(engine) as s, s.begin():
        result = await s.execute(RESET_SCAN, {"u": user_id})
        return bool(result.rowcount)  # type: ignore[attr-defined]
