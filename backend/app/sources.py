"""Folder của người dùng trên server và xóa mail của một folder.

Spec: docs/superpowers/specs/2026-10-17-delete-folder-mail-design.md. Chạy trong user_session (RLS):
chỉ thấy và chỉ xóa được dữ liệu của chính người dùng.
"""

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.mail.domains import inbox_path_sql
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
# Số mail theo folder hiệu lực cho web (spec 26 mục 8): cùng quy tắc với filter_conditions. Mail đã
# nằm thật ở folder đích đã được đếm theo ownership, không đếm lại theo run. LEFT JOIN + IS NULL để
# planner dùng anti join: NOT (Inbox AND EXISTS …) dò từng dòng, chậm gấp 10 lần trên 50.000 mail.
LIST_FOLDERS = text(
    f"""
    SELECT s.id, s.folder_path, s.account, s.kind, s.last_scan_at,
           (SELECT count(*) FROM message_ownership o
            LEFT JOIN message_auto_runs m ON m.message_id = o.message_id
                 AND m.user_id = o.user_id AND m.target_source_id IS NOT NULL
                 AND {inbox_path_sql("s.folder_path")}
            WHERE o.source_id = s.id AND o.user_id = s.user_id AND NOT o.is_deleted
              AND m.message_id IS NULL)
           + (SELECT count(*) FROM message_auto_runs r
              WHERE r.user_id = s.user_id AND r.target_source_id = s.id
                AND NOT EXISTS (SELECT 1 FROM message_ownership o
                                WHERE o.message_id = r.message_id AND o.user_id = r.user_id
                                  AND o.source_id = s.id AND NOT o.is_deleted)) AS message_count
    FROM mail_sources s
    ORDER BY s.folder_path
    """
)


async def list_sources(session: AsyncSession) -> list[SourceInfo]:
    """Số mail vật lý theo ownership: hợp đồng với collector, không đổi."""
    return await _source_infos(session, LIST_SOURCES)


async def list_folders(session: AsyncSession) -> list[SourceInfo]:
    """Số mail theo folder hiệu lực: chỉ cho GET /folders của web."""
    return await _source_infos(session, LIST_FOLDERS)


async def _source_infos(session: AsyncSession, query: TextClause) -> list[SourceInfo]:
    rows = await session.execute(query)
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
