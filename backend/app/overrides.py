"""Gộp/tách thread bằng tay từ web (spec 2026-10-18-thread-api-design.md mục 5).

Chạy trong user_session (app_api + RLS). Ghi thread_overrides và tăng
user_thread_state.overrides_version trong cùng transaction; worker áp dụng trong vài giây
(task apply_overrides). Lỗi người dùng sửa được → OverrideError (có mã cho web).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.web_schemas import MessageBrief, OverrideCreate, OverrideInfo


class OverrideError(Exception):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.detail = detail


MESSAGE = text("SELECT id, user_id, thread_id, thread_method FROM messages WHERE id = :m")
BUMP_VERSION = text(
    """
    INSERT INTO user_thread_state (user_id, overrides_version) VALUES (:u, 1)
    ON CONFLICT (user_id) DO UPDATE
    SET overrides_version = user_thread_state.overrides_version + 1, updated_at = now()
    """
)
EXISTING = text(
    "SELECT id FROM thread_overrides WHERE user_id = :u AND action = :a AND message_id = :m "
    "AND target_message_id IS NOT DISTINCT FROM :t ORDER BY id LIMIT 1"
)
INSERT = text(
    "INSERT INTO thread_overrides (user_id, action, message_id, target_message_id) "
    "VALUES (:u, :a, :m, :t) RETURNING id"
)
LIST = text(
    """
    SELECT o.id, o.action, o.created_at,
           m.id AS m_id, m.thread_id AS m_thread, m.subject AS m_subject,
           m.from_name AS m_from_name, m.from_addr AS m_from_addr, m.sent_at AS m_sent_at,
           t.id AS t_id, t.thread_id AS t_thread, t.subject AS t_subject,
           t.from_name AS t_from_name, t.from_addr AS t_from_addr, t.sent_at AS t_sent_at
    FROM thread_overrides o
    JOIN messages m ON m.id = o.message_id AND m.user_id = o.user_id
    LEFT JOIN messages t ON t.id = o.target_message_id AND t.user_id = o.user_id
    WHERE o.user_id = :u AND (CAST(:id AS bigint) IS NULL OR o.id = CAST(:id AS bigint))
    ORDER BY o.created_at DESC, o.id DESC
    """
)


def _brief(r: Any, prefix: str) -> MessageBrief | None:
    if getattr(r, f"{prefix}_id") is None:
        return None
    return MessageBrief(
        id=getattr(r, f"{prefix}_id"),
        thread_id=getattr(r, f"{prefix}_thread"),
        subject=getattr(r, f"{prefix}_subject"),
        from_name=getattr(r, f"{prefix}_from_name"),
        from_addr=getattr(r, f"{prefix}_from_addr"),
        sent_at=getattr(r, f"{prefix}_sent_at"),
    )


async def list_overrides(
    session: AsyncSession, user_id: int, override_id: int | None = None
) -> list[OverrideInfo]:
    # Sửa tay là của riêng người dùng: lọc user_id tường minh, không chỉ dựa vào RLS.
    rows = await session.execute(LIST, {"u": user_id, "id": override_id})
    out: list[OverrideInfo] = []
    for r in rows:
        message = _brief(r, "m")
        assert message is not None
        out.append(
            OverrideInfo(
                id=r.id,
                action=r.action,
                created_at=r.created_at,
                message=message,
                target=_brief(r, "t"),
            )
        )
    return out


async def _own_message(session: AsyncSession, user_id: int, message_id: int) -> Any:
    row = (await session.execute(MESSAGE, {"m": message_id})).first()
    if row is None or row.user_id != user_id:
        raise OverrideError(404, "message_not_found", "Không có mail này")
    if row.thread_id is None:
        raise OverrideError(409, "not_threaded", "Mail đang chờ gom thread, thử lại sau")
    return row


async def create_override(
    session: AsyncSession, user_id: int, req: OverrideCreate
) -> tuple[OverrideInfo, bool]:
    """(sửa tay, created). Sửa tay giống hệt đã có → trả bản đã có, created = False."""
    message = await _own_message(session, user_id, req.message_id)
    if req.action == "split":
        if message.thread_method == "root":
            raise OverrideError(
                400, "cannot_split_root", "Mail đầu thread không tách được (không có gì để tách)"
            )
    else:
        assert req.target_message_id is not None
        target = await _own_message(session, user_id, req.target_message_id)
        if target.thread_id == message.thread_id:
            raise OverrideError(400, "same_thread", "Hai mail đang ở cùng một thread")
    params = {"u": user_id, "a": req.action, "m": req.message_id, "t": req.target_message_id}
    existing = await session.scalar(EXISTING, params)
    created = existing is None
    if created:
        existing = await session.scalar(INSERT, params)
        await session.execute(BUMP_VERSION, {"u": user_id})
    (info,) = await list_overrides(session, user_id, existing)
    return info, created


async def delete_override(session: AsyncSession, user_id: int, override_id: int) -> bool:
    deleted = await session.scalar(
        text("DELETE FROM thread_overrides WHERE id = :id AND user_id = :u RETURNING id"),
        {"id": override_id, "u": user_id},
    )
    if deleted is None:
        return False
    await session.execute(BUMP_VERSION, {"u": user_id})
    return True
