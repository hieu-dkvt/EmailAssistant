"""Ghi một lô mail vào DB của người dùng (chạy trong user_session, RLS áp dụng).

Spec: docs/superpowers/specs/2026-10-17-ingest-api-design.md mục 3. Không bao giờ dùng
ON CONFLICT DO UPDATE trên messages: mail giả cùng Message-ID không được ghi đè bản thật.
"""

import json
import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.mail.hashing import message_id_hash, normalize_message_id
from app.schemas import Address, IngestBatch, IngestMessage, IngestResult

# Parser cũ hơn bản này gửi nội dung file đính kèm text trong body_text (security review
# 2026-10-17): không nhận dữ liệu từ collector cũ.
MIN_PARSER_VERSION = (0, 4, 0)
_PARSER_VERSION_RE = re.compile(r"collector/(\d+)\.(\d+)\.(\d+)")


def parser_supported(parser_version: str) -> bool:
    m = _PARSER_VERSION_RE.fullmatch(parser_version)
    return m is not None and tuple(int(x) for x in m.groups()) >= MIN_PARSER_VERSION


UPSERT_SOURCE = text(
    """
    INSERT INTO mail_sources
        (user_id, kind, account, folder_path, file_size, last_offset, last_mtime, last_scan_at)
    VALUES (:user_id, :kind, :account, :folder_path, :file_size, :last_offset, :last_mtime, now())
    ON CONFLICT (user_id, folder_path) DO UPDATE SET
        kind = excluded.kind, account = excluded.account, file_size = excluded.file_size,
        last_offset = excluded.last_offset, last_mtime = excluded.last_mtime, last_scan_at = now()
    RETURNING id
    """
)

INSERT_MESSAGE = text(
    """
    INSERT INTO messages
        (user_id, message_id_hash, message_id, subject, from_addr, from_name, to_addrs, cc_addrs,
         sent_at, sent_tz_offset_min, in_reply_to, references_ids, thread_index, body_text,
         body_from_html, has_attachments, attachments, parser_version)
    VALUES
        (:user_id, :hash, :message_id, :subject, :from_addr, :from_name,
         CAST(:to_addrs AS jsonb), CAST(:cc_addrs AS jsonb), :sent_at, :tz, :in_reply_to,
         :references_ids, :thread_index, :body_text, :body_from_html, :has_attachments,
         CAST(:attachments AS jsonb), :parser_version)
    ON CONFLICT (user_id, message_id_hash) DO NOTHING
    RETURNING id
    """
)

SELECT_EXISTING = text(
    "SELECT id, from_addr, sent_at FROM messages "
    "WHERE user_id = :user_id AND message_id_hash = :hash"
)

UPSERT_OWNERSHIP = text(
    """
    INSERT INTO message_ownership
        (message_id, source_id, user_id, mbox_offset, is_read, is_replied, is_flagged)
    VALUES (:message_id, :source_id, :user_id, :mbox_offset, :is_read, :is_replied, :is_flagged)
    ON CONFLICT (message_id, source_id) DO UPDATE SET
        mbox_offset = excluded.mbox_offset, is_read = excluded.is_read,
        is_replied = excluded.is_replied, is_flagged = excluded.is_flagged,
        last_seen_at = now(), is_deleted = false
    """
)


def _addr(a: Address) -> dict[str, str | None]:
    return {"name": a.name, "addr": a.addr.strip().lower()}


def _same_time(a: datetime | None, b: datetime | None) -> bool:
    return a == b if (a is None) == (b is None) else False


async def ingest_batch(session: AsyncSession, user_id: int, batch: IngestBatch) -> IngestResult:
    src = batch.source
    source_id: int = await session.scalar(
        UPSERT_SOURCE,
        {
            "user_id": user_id,
            "kind": src.kind,
            "account": src.account,
            "folder_path": src.folder_path,
            "file_size": src.file_size,
            "last_offset": src.last_offset,
            "last_mtime": src.last_mtime,
        },
    )

    inserted = duplicates = mismatched = 0
    for m in batch.messages:
        message_id, is_new, same = await _insert_message(session, user_id, batch.parser_version, m)
        if is_new:
            inserted += 1
        else:
            duplicates += 1
        if not same:
            # Trùng Message-ID nhưng khác người gửi/ngày: có thể là mail giả. Không gắn nó
            # vào bản thật, để vị trí mbox của bản thật không bị trỏ sang mail giả.
            mismatched += 1
            continue
        await session.execute(
            UPSERT_OWNERSHIP,
            {
                "message_id": message_id,
                "source_id": source_id,
                "user_id": user_id,
                "mbox_offset": m.mbox_offset,
                "is_read": m.flags.read,
                "is_replied": m.flags.replied,
                "is_flagged": m.flags.flagged,
            },
        )
    return IngestResult(
        source_id=source_id,
        received=len(batch.messages),
        inserted=inserted,
        duplicates=duplicates,
        mismatched=mismatched,
    )


async def _insert_message(
    session: AsyncSession, user_id: int, parser_version: str, m: IngestMessage
) -> tuple[int, bool, bool]:
    """Trả (id, là mail mới, khớp dấu vân tay với bản đã có)."""
    from_addr = m.from_.addr.strip().lower() if m.from_ else None
    h = message_id_hash(m.message_id, from_addr, m.date, m.subject, m.body_text)
    new_id = await session.scalar(
        INSERT_MESSAGE,
        {
            "user_id": user_id,
            "hash": h,
            "message_id": normalize_message_id(m.message_id),
            "subject": m.subject,
            "from_addr": from_addr,
            "from_name": m.from_.name if m.from_ else None,
            "to_addrs": json.dumps([_addr(a) for a in m.to], ensure_ascii=False),
            "cc_addrs": json.dumps([_addr(a) for a in m.cc], ensure_ascii=False),
            "sent_at": m.date,
            "tz": m.date_tz_offset_min,
            "in_reply_to": normalize_message_id(m.in_reply_to),
            "references_ids": [r for r in (normalize_message_id(x) for x in m.references) if r],
            "thread_index": m.thread_index,
            "body_text": m.body_text,
            "body_from_html": m.body_from_html,
            "has_attachments": bool(m.attachments),
            "attachments": json.dumps([a.model_dump() for a in m.attachments], ensure_ascii=False),
            "parser_version": parser_version,
        },
    )
    if new_id is not None:
        return new_id, True, True
    existing = (await session.execute(SELECT_EXISTING, {"user_id": user_id, "hash": h})).one()
    same = existing.from_addr == from_addr and _same_time(existing.sent_at, m.date)
    return existing.id, False, same
