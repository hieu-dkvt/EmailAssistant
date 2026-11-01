"""Đọc thread và mail cho web (spec 2026-10-18-thread-api-design.md mục 4).

Chạy trong user_session (app_api + RLS). Join giữa các bảng mail luôn kèm user_id của dòng bên trái:
khi có chia sẻ theo dự án RLS sẽ cho thấy dòng của người khác, nhưng không được ghép mail của người
này vào thread của người kia.
"""

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.mail.cleaning import CLEANER_VERSION
from app.search.highlight import highlight
from app.search.keyword import MessageFilters, filter_conditions, folders_of
from app.web_schemas import (
    AddressOut,
    AttachmentOut,
    FolderRef,
    MessageFull,
    MessageRef,
    PartKind,
    ProcessingStatus,
    ProjectRef,
    TextPart,
    ThreadDetail,
    ThreadMessage,
    ThreadPage,
    ThreadSummary,
)

THREAD_MESSAGE_LIMIT = 1000
BODY_LIMIT = 200_000
THREAD_BODY_BUDGET = 2_000_000
UNPROCESSED_COUNT_CAP = 10_001


class BadCursor(ValueError):
    pass


def encode_cursor(last_message_at: datetime | None, thread_id: int) -> str:
    raw = json.dumps([last_message_at.isoformat() if last_message_at else None, thread_id])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime | None, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, thread_id = json.loads(raw)
        if not isinstance(thread_id, int) or isinstance(thread_id, bool):
            raise BadCursor
        return (datetime.fromisoformat(at) if at is not None else None), thread_id
    except (ValueError, TypeError, binascii.Error) as e:
        raise BadCursor from e


PROJECTS_OF = text(
    """
    SELECT DISTINCT m.thread_id, p.id, p.name
    FROM messages m
    JOIN message_projects mp ON mp.message_id = m.id AND mp.user_id = m.user_id
    JOIN projects p ON p.id = mp.project_id AND p.user_id = mp.user_id
    WHERE m.thread_id = ANY(CAST(:ids AS bigint[])) AND m.user_id = app_user_id()
    ORDER BY m.thread_id, p.name, p.id
    """
)


async def projects_of(session: AsyncSession, thread_ids: list[int]) -> dict[int, list[ProjectRef]]:
    """Dự án của từng thread (qua mọi mail), sắp theo tên. Thread không có dự án: không có key."""
    out: dict[int, list[ProjectRef]] = {}
    if not thread_ids:
        return out
    for r in await session.execute(PROJECTS_OF, {"ids": thread_ids}):
        out.setdefault(r.thread_id, []).append(ProjectRef(id=r.id, name=r.name))
    return out


async def list_threads(
    session: AsyncSession, filters: MessageFilters, cursor: str | None, limit: int
) -> ThreadPage:
    conds, params = filter_conditions(filters, alias="f")
    where: list[str] = []
    if conds:
        where.append(
            "EXISTS (SELECT 1 FROM messages f WHERE f.thread_id = t.id AND f.user_id = t.user_id "
            "AND " + " AND ".join(conds) + ")"
        )
    if cursor is not None:
        at, after_id = decode_cursor(cursor)
        if at is None:
            where.append("(t.last_message_at IS NULL AND t.id < :after_id)")
        else:
            where.append(
                "(t.last_message_at < :after_at OR (t.last_message_at = :after_at AND t.id < "
                ":after_id) OR t.last_message_at IS NULL)"
            )
            params["after_at"] = at
        params["after_id"] = after_id
    rows = (
        await session.execute(
            text(
                f"""
                SELECT t.id, t.message_count, t.first_message_at, t.last_message_at,
                       r.subject, l.from_name, l.from_addr, a.found IS NOT NULL AS has_attachments
                FROM threads t
                LEFT JOIN LATERAL (
                    SELECT subject FROM messages WHERE thread_id = t.id AND user_id = t.user_id
                    AND thread_method = 'root' LIMIT 1
                ) r ON true
                LEFT JOIN LATERAL (
                    SELECT from_name, from_addr FROM messages
                    WHERE thread_id = t.id AND user_id = t.user_id
                    ORDER BY sent_at DESC NULLS LAST, id DESC LIMIT 1
                ) l ON true
                -- LATERAL ... LIMIT 1 thay cho EXISTS trong SELECT: EXISTS bị planner đổi thành
                -- subplan băm quét cả bảng messages.
                LEFT JOIN LATERAL (
                    SELECT true AS found FROM messages
                    WHERE thread_id = t.id AND user_id = t.user_id AND has_attachments LIMIT 1
                ) a ON true
                WHERE {" AND ".join(where) if where else "true"}
                ORDER BY t.last_message_at DESC NULLS LAST, t.id DESC
                LIMIT :limit
                """
            ),
            {**params, "limit": limit + 1},
        )
    ).all()
    page_rows = rows[:limit]
    projects = await projects_of(session, [r.id for r in page_rows])
    items = [
        ThreadSummary(
            id=r.id,
            subject=r.subject,
            message_count=r.message_count,
            first_message_at=r.first_message_at,
            last_message_at=r.last_message_at,
            last_from_name=r.from_name,
            last_from_addr=r.from_addr,
            has_attachments=r.has_attachments,
            projects=projects.get(r.id, []),
        )
        for r in page_rows
    ]
    next_cursor = (
        encode_cursor(items[-1].last_message_at, items[-1].id) if len(rows) > limit else None
    )
    return ThreadPage(items=items, next_cursor=next_cursor)


THREAD = text(
    """
    SELECT t.id, t.message_count, t.first_message_at, t.last_message_at, r.subject
    FROM threads t
    LEFT JOIN LATERAL (
        SELECT subject FROM messages WHERE thread_id = t.id AND user_id = t.user_id
        AND thread_method = 'root' LIMIT 1
    ) r ON true
    WHERE t.id = :t
    """
)
# Mỗi mail tối đa :body_limit ký tự, cả thread tối đa :budget ký tự (mail sau nhận phần còn lại):
# thread hàng trăm mail dài không làm response hàng trăm MB. Phần bị cắt xem ở "Toàn văn".
THREAD_MESSAGES = text(
    """
    SELECT b.id, b.thread_parent_id, b.thread_method, b.sent_at, b.from_name, b.from_addr,
           b.to_addrs, b.cc_addrs, b.subject, b.has_attachments, b.attachments, b.cleaned,
           b.cut_count, left(b.full_body, b.allowed) AS body,
           char_length(b.full_body) > b.allowed AS body_truncated
    FROM (
        SELECT m.id, m.thread_parent_id, m.thread_method, m.sent_at, m.from_name, m.from_addr,
               m.to_addrs, m.cc_addrs, m.subject, m.has_attachments, m.attachments,
               m.body_clean IS NOT NULL AS cleaned,
               coalesce(jsonb_array_length(m.clean_cuts), 0) AS cut_count,
               coalesce(m.body_clean, m.body_text) AS full_body,
               CAST(greatest(0, least(CAST(:body_limit AS integer),
                   CAST(:budget AS bigint) - coalesce(sum(least(
                       char_length(coalesce(m.body_clean, m.body_text)),
                       CAST(:body_limit AS integer)
                   )) OVER (ORDER BY m.sent_at NULLS LAST, m.id
                            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)
               )) AS integer) AS allowed
        FROM messages m JOIN threads t ON t.id = m.thread_id AND t.user_id = m.user_id
        WHERE t.id = :t
        ORDER BY m.sent_at NULLS LAST, m.id
        LIMIT :limit
    ) b
    ORDER BY b.sent_at NULLS LAST, b.id
    """
)


def _addresses(value: Any) -> list[AddressOut]:
    return [
        AddressOut(name=a.get("name"), addr=a["addr"])
        for a in value or []
        if isinstance(a, dict) and a.get("addr")
    ]


def _attachments(value: Any) -> list[AttachmentOut]:
    return [
        AttachmentOut(
            filename=a.get("filename"),
            size=int(a.get("size") or 0),
            content_type=a.get("content_type"),
        )
        for a in value or []
        if isinstance(a, dict)
    ]


async def get_thread(
    session: AsyncSession, thread_id: int, keywords: list[str]
) -> ThreadDetail | None:
    t = (await session.execute(THREAD, {"t": thread_id})).first()
    if t is None:
        return None
    rows = (
        await session.execute(
            THREAD_MESSAGES,
            {
                "t": thread_id,
                "body_limit": BODY_LIMIT,
                "budget": THREAD_BODY_BUDGET,
                "limit": THREAD_MESSAGE_LIMIT + 1,
            },
        )
    ).all()
    shown = rows[:THREAD_MESSAGE_LIMIT]
    folders = await folders_of(session, [r.id for r in shown])
    # Tô từ khóa tốn CPU (tới vài giây với thread lớn): chạy ở thread riêng, không chặn event loop.
    messages = await run_in_threadpool(_thread_messages, shown, folders, keywords)
    return ThreadDetail(
        id=t.id,
        subject=t.subject,
        message_count=t.message_count,
        first_message_at=t.first_message_at,
        last_message_at=t.last_message_at,
        keywords=keywords,
        truncated=len(rows) > THREAD_MESSAGE_LIMIT,
        messages=messages,
    )


def _thread_messages(
    rows: Sequence[Any], folders: dict[int, list[FolderRef]], keywords: list[str]
) -> list[ThreadMessage]:
    return [
        ThreadMessage(
            id=r.id,
            thread_parent_id=r.thread_parent_id,
            thread_method=r.thread_method,
            sent_at=r.sent_at,
            from_name=r.from_name,
            from_addr=r.from_addr,
            to=_addresses(r.to_addrs),
            cc=_addresses(r.cc_addrs),
            subject=highlight(r.subject, keywords),
            body=highlight(r.body, keywords),
            cleaned=r.cleaned,
            cut_count=r.cut_count,
            body_truncated=r.body_truncated,
            has_attachments=r.has_attachments,
            attachments=_attachments(r.attachments),
            folders=folders.get(r.id, []),
        )
        for r in rows
    ]


async def message_ref(session: AsyncSession, message_id: int) -> MessageRef | None:
    row = (
        await session.execute(
            text("SELECT id, thread_id FROM messages WHERE id = :m"), {"m": message_id}
        )
    ).first()
    return MessageRef(id=row.id, thread_id=row.thread_id) if row else None


def split_parts(body: str, cuts: Any) -> list[tuple[PartKind, str]]:
    """Chia body_text theo clean_cuts thành (kind, text) theo thứ tự. Cut hỏng bị bỏ qua."""
    valid: list[tuple[int, int, PartKind]] = sorted(
        (c["start"], c["end"], c["kind"])
        for c in cuts or []
        if isinstance(c, dict)
        and c.get("kind") in ("quote", "signature")
        and isinstance(c.get("start"), int)
        and isinstance(c.get("end"), int)
        and 0 <= c["start"] < c["end"] <= len(body)
    )
    parts: list[tuple[PartKind, str]] = []
    pos = 0
    for start, end, kind in valid:
        if start < pos:  # chồng lên đoạn trước: bỏ
            continue
        if start > pos:
            parts.append(("body", body[pos:start]))
        parts.append((kind, body[start:end]))
        pos = end
    if pos < len(body):
        parts.append(("body", body[pos:]))
    return parts


async def message_full(
    session: AsyncSession, message_id: int, keywords: list[str]
) -> MessageFull | None:
    row = (
        await session.execute(
            text("SELECT id, body_text, clean_cuts FROM messages WHERE id = :m"), {"m": message_id}
        )
    ).first()
    if row is None:
        return None

    def build() -> list[TextPart]:
        return [
            TextPart(kind=kind, segments=highlight(chunk, keywords))
            for kind, chunk in split_parts(row.body_text, row.clean_cuts)
        ]

    # body_text tối đa 2.000.000 ký tự (giới hạn của ingest); tô từ khóa ở thread riêng.
    return MessageFull(id=row.id, keywords=keywords, parts=await run_in_threadpool(build))


async def processing_status(session: AsyncSession) -> ProcessingStatus:
    row = (
        await session.execute(
            text(
                """
                SELECT
                  coalesce((SELECT overrides_version > applied_overrides_version
                            FROM user_thread_state WHERE user_id = app_user_id()), false)
                      AS pending,
                  (SELECT count(*) FROM (
                      SELECT 1 FROM messages
                      WHERE user_id = app_user_id()
                        AND (cleaner_version IS DISTINCT FROM :cv OR thread_id IS NULL)
                      LIMIT :cap) x) AS unprocessed
                """
            ),
            {"cv": CLEANER_VERSION, "cap": UNPROCESSED_COUNT_CAP},
        )
    ).one()
    return ProcessingStatus(rethread_pending=row.pending, unprocessed_messages=row.unprocessed)
