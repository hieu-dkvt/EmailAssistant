"""Folder của người dùng trên server và xóa mail của một folder.

Spec: docs/superpowers/specs/2026-10-17-delete-folder-mail-design.md. Chạy trong user_session (RLS):
chỉ thấy và chỉ xóa được dữ liệu của chính người dùng.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas import DeleteResult, SourceInfo

LIST_SOURCES = text(
    """
    SELECT s.id, s.folder_path, s.account, s.kind, s.last_scan_at,
           count(o.message_id) FILTER (WHERE NOT o.is_deleted) AS message_count
    FROM mail_sources s
    LEFT JOIN message_ownership o ON o.source_id = s.id
    GROUP BY s.id
    ORDER BY s.folder_path
    """
)


async def list_sources(session: AsyncSession) -> list[SourceInfo]:
    rows = await session.execute(LIST_SOURCES)
    return [
        SourceInfo(
            id=r.id,
            folder_path=r.folder_path,
            account=r.account,
            kind=r.kind,
            message_count=r.message_count,
            last_scan_at=r.last_scan_at,
        )
        for r in rows
    ]


async def delete_folder_mail(
    session: AsyncSession, user_id: int, folder_path: str
) -> DeleteResult | None:
    """Xóa folder và các mail không còn nằm ở folder nào khác. Folder không có → None."""
    source_id = await session.scalar(
        text("SELECT id FROM mail_sources WHERE user_id = :u AND folder_path = :p"),
        {"u": user_id, "p": folder_path},
    )
    if source_id is None:
        return None
    message_ids = list(
        await session.scalars(
            text("SELECT message_id FROM message_ownership WHERE source_id = :s"),
            {"s": source_id},
        )
    )
    # Xóa folder: ownership của folder bị xóa theo (FK ON DELETE CASCADE).
    await session.execute(text("DELETE FROM mail_sources WHERE id = :s"), {"s": source_id})
    deleted = len(
        list(
            await session.scalars(
                text(
                    "DELETE FROM messages m WHERE m.id = ANY(:ids) "
                    "AND NOT EXISTS (SELECT 1 FROM message_ownership o WHERE o.message_id = m.id) "
                    "RETURNING m.id"
                ),
                {"ids": message_ids},
            )
        )
    )
    return DeleteResult(deleted_messages=deleted, kept_messages=len(message_ids) - deleted)
