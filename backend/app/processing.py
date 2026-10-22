"""Làm sạch và gom thread trong DB, theo từng người dùng (spec 2026-10-17-content-cleaning mục 5,
2026-10-17-threading mục 4.6 và 6, 2026-10-17-worker mục 4).

Chạy bằng role app_worker (BYPASSRLS): MỌI truy vấn phải có user_id = :u. Không bao giờ đọc hay
gom mail của hai người dùng với nhau. Log chỉ có id, số lượng, thời gian.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.config import database_url
from app.mail.cleaning import CLEANER_VERSION, clean_body
from app.mail.threading import (
    THREADER_VERSION,
    MailRow,
    Override,
    assign_thread_ids,
    build_threads,
)

log = logging.getLogger("app.processing")

CLEAN_BATCH = 500
CLEAN_BATCH_MAX_BYTES = 16 * 1024 * 1024

# Khóa theo người dùng: worker và CLI không làm sạch / gom chồng nhau cho cùng một người.
LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended('process-mail:' || :u, 0))")


def create_worker_engine(url: str | None = None, **kwargs: Any) -> AsyncEngine:
    """Engine async bằng role app_worker. hide_parameters: nội dung mail không vào exception/log."""
    return create_async_engine(
        url or database_url("worker"), pool_pre_ping=True, hide_parameters=True, **kwargs
    )


@dataclass
class CleanStats:
    cleaned: int = 0
    failed: int = 0


@dataclass
class ThreadStats:
    messages: int
    threads: int
    changed: int  # số mail đổi thread_id / thread_parent_id / thread_method
    created: int
    deleted: int


@dataclass
class ProcessResult:
    clean: CleanStats
    threads: ThreadStats | None  # None: không cần gom lại


# --- làm sạch ---

# Lô: tối đa CLEAN_BATCH mail và CLEAN_BATCH_MAX_BYTES (luôn ít nhất một mail). Phân trang theo
# id (keyset) để mỗi lô không quét lại từ đầu.
PENDING_CLEAN_IDS = text(
    """
    SELECT id FROM (
        SELECT id,
               sum(size) OVER (ORDER BY id) AS total,
               row_number() OVER (ORDER BY id) AS n
        FROM (
            SELECT id, octet_length(body_text) AS size FROM messages
            WHERE user_id = :u AND id > :after AND cleaner_version IS DISTINCT FROM :v
            ORDER BY id LIMIT :limit
        ) p
    ) c
    WHERE n = 1 OR total <= :max_bytes
    ORDER BY id
    """
)
SELECT_BODIES = text(
    "SELECT id, subject, body_text FROM messages "
    "WHERE user_id = :u AND id = ANY(CAST(:ids AS bigint[])) ORDER BY id"
)
UPDATE_CLEAN = text(
    """
    UPDATE messages m
    SET body_clean = u.body_clean, clean_cuts = u.cuts, cleaner_version = :v
    FROM unnest(CAST(:ids AS bigint[]), CAST(:bodies AS text[]), CAST(:cuts AS jsonb[]))
         AS u(id, body_clean, cuts)
    WHERE m.id = u.id AND m.user_id = :u
    """
)


async def clean_pending(engine: AsyncEngine, user_id: int) -> CleanStats:
    """Làm sạch mail có cleaner_version khác bản hiện hành; mỗi lô một transaction."""
    stats = CleanStats()
    after = 0
    while True:
        async with AsyncSession(engine) as s, s.begin():
            await s.execute(LOCK, {"u": str(user_id)})
            ids = list(
                await s.scalars(
                    PENDING_CLEAN_IDS,
                    {
                        "u": user_id,
                        "after": after,
                        "v": CLEANER_VERSION,
                        "limit": CLEAN_BATCH,
                        "max_bytes": CLEAN_BATCH_MAX_BYTES,
                    },
                )
            )
            if not ids:
                return stats
            rows = (await s.execute(SELECT_BODIES, {"u": user_id, "ids": ids})).all()
            bodies: list[str] = []
            cuts: list[str] = []
            for r in rows:
                try:
                    result = clean_body(r.body_text, r.subject)
                    bodies.append(result.body_clean)
                    cuts.append(json.dumps([asdict(c) for c in result.cuts]))
                except Exception as e:  # bug trong quy tắc: không chặn cả lô (spec 9 mục 5)
                    log.warning(
                        "clean failed user=%s message=%s error=%s", user_id, r.id, type(e).__name__
                    )
                    bodies.append(r.body_text)
                    cuts.append("[]")
                    stats.failed += 1
            await s.execute(
                UPDATE_CLEAN,
                {
                    "u": user_id,
                    "v": CLEANER_VERSION,
                    "ids": [r.id for r in rows],
                    "bodies": bodies,
                    "cuts": cuts,
                },
            )
            stats.cleaned += len(rows)
            after = ids[-1]


# --- gom thread ---

SELECT_THREADING = text(
    """
    SELECT id, message_id, in_reply_to, references_ids, thread_index, subject, from_addr,
           ARRAY(
               SELECT lower(e ->> 'addr') FROM jsonb_array_elements(to_addrs || cc_addrs) e
               WHERE e ->> 'addr' IS NOT NULL
           ) AS recipients,
           sent_at, thread_id, thread_parent_id, thread_method
    FROM messages WHERE user_id = :u
    """
)
SELECT_OVERRIDES = text(
    "SELECT action, message_id, target_message_id FROM thread_overrides "
    "WHERE user_id = :u ORDER BY created_at, id"
)
ALLOCATE_THREAD_IDS = text(
    "SELECT nextval(pg_get_serial_sequence('threads', 'id')) FROM generate_series(1, :n)"
)
# threads chỉ có số đếm và ngày, không chép nội dung mail (subject đọc từ mail root): xóa mail
# của folder thì không còn bản sao nội dung nào trong threads (migration 0009).
INSERT_THREADS = text(
    """
    INSERT INTO threads (id, user_id, message_count, first_message_at, last_message_at,
                         threader_version)
    OVERRIDING SYSTEM VALUE
    SELECT t.id, :u, t.n, t.first_at, t.last_at, :tv
    FROM unnest(CAST(:ids AS bigint[]), CAST(:counts AS integer[]),
                CAST(:firsts AS timestamptz[]), CAST(:lasts AS timestamptz[]))
         AS t(id, n, first_at, last_at)
    """
)
UPDATE_THREADS = text(
    """
    UPDATE threads th
    SET message_count = t.n, first_message_at = t.first_at, last_message_at = t.last_at,
        threader_version = :tv, updated_at = now()
    FROM unnest(CAST(:ids AS bigint[]), CAST(:counts AS integer[]),
                CAST(:firsts AS timestamptz[]), CAST(:lasts AS timestamptz[]))
         AS t(id, n, first_at, last_at)
    WHERE th.id = t.id AND th.user_id = :u
      AND (th.message_count, th.first_message_at, th.last_message_at, th.threader_version)
          IS DISTINCT FROM (t.n, t.first_at, t.last_at, :tv)
    """
)
UPDATE_MESSAGE_THREADS = text(
    """
    UPDATE messages m
    SET thread_id = u.thread_id, thread_parent_id = u.parent_id, thread_method = u.method
    FROM unnest(CAST(:ids AS bigint[]), CAST(:threads AS bigint[]), CAST(:parents AS bigint[]),
                CAST(:methods AS text[]))
         AS u(id, thread_id, parent_id, method)
    WHERE m.id = u.id AND m.user_id = :u
    """
)
DELETE_OTHER_THREADS = text(
    "DELETE FROM threads WHERE user_id = :u AND id <> ALL(CAST(:keep AS bigint[])) RETURNING id"
)

NEEDS_RETHREAD = text(
    """
    SELECT EXISTS (SELECT 1 FROM messages WHERE user_id = :u AND thread_id IS NULL)
        OR EXISTS (SELECT 1 FROM threads WHERE user_id = :u AND threader_version <> :tv)
        OR EXISTS (SELECT 1 FROM user_thread_state
                   WHERE user_id = :u AND overrides_version > applied_overrides_version)
        OR (SELECT count(*) FROM messages WHERE user_id = :u)
           <> (SELECT coalesce(sum(message_count), 0) FROM threads WHERE user_id = :u)
    """
)
# Sửa tay từ web (spec 2026-10-18-thread-api mục 6): đọc version TRƯỚC khi đọc thread_overrides.
# READ COMMITTED: câu sau thấy mọi thứ câu trước thấy, nên sửa tay commit trước lúc đọc version đã
# nằm trong lần đọc override; sửa tay commit sau làm version lớn hơn → lần sau gom tiếp.
SELECT_OVERRIDES_VERSION = text(
    "SELECT overrides_version FROM user_thread_state WHERE user_id = :u"
)
MARK_OVERRIDES_APPLIED = text(
    "UPDATE user_thread_state SET applied_overrides_version = :v "
    "WHERE user_id = :u AND applied_overrides_version < :v"
)


async def needs_rethread(engine: AsyncEngine, user_id: int) -> bool:
    async with AsyncSession(engine) as s:
        return bool(await s.scalar(NEEDS_RETHREAD, {"u": user_id, "tv": THREADER_VERSION}))


async def rethread_user(engine: AsyncEngine, user_id: int) -> ThreadStats:
    """Gom lại toàn bộ mail của một người dùng trong một transaction, giữ id thread ổn định."""
    async with AsyncSession(engine) as s, s.begin():
        await s.execute(LOCK, {"u": str(user_id)})
        overrides_version = await s.scalar(SELECT_OVERRIDES_VERSION, {"u": user_id})
        rows = (await s.execute(SELECT_THREADING, {"u": user_id})).all()
        overrides = [
            Override(o.action, o.message_id, o.target_message_id)
            for o in await s.execute(SELECT_OVERRIDES, {"u": user_id})
        ]
        mails = {
            r.id: MailRow(
                id=r.id,
                message_id=r.message_id,
                in_reply_to=r.in_reply_to,
                references=tuple(r.references_ids),
                thread_index=r.thread_index,
                subject=r.subject,
                from_addr=r.from_addr,
                recipients=frozenset(r.recipients),
                sent_at=r.sent_at,
            )
            for r in rows
        }
        result = build_threads(mails.values(), overrides)
        old = {r.id: (r.thread_id, r.thread_parent_id, r.thread_method) for r in rows}
        assigned = assign_thread_ids(result.groups, {m: t[0] for m, t in old.items()})

        n_new = assigned.count(None)
        new_ids = iter(list(await s.scalars(ALLOCATE_THREAD_IDS, {"n": n_new})) if n_new else [])
        thread_ids = [t if t is not None else next(new_ids) for t in assigned]

        new_rows = _thread_columns()
        kept_rows = _thread_columns()
        for group, tid, was_new in zip(
            result.groups, thread_ids, (t is None for t in assigned), strict=True
        ):
            dates = [d for m in group if (d := mails[m].sent_at) is not None]
            target = new_rows if was_new else kept_rows
            target["ids"].append(tid)
            target["counts"].append(len(group))
            target["firsts"].append(min(dates, default=None))
            target["lasts"].append(max(dates, default=None))
        params = {"u": user_id, "tv": THREADER_VERSION}
        if n_new:
            await s.execute(INSERT_THREADS, {**params, **new_rows})
        if kept_rows["ids"]:
            await s.execute(UPDATE_THREADS, {**params, **kept_rows})

        changes: dict[str, list[Any]] = {"ids": [], "threads": [], "parents": [], "methods": []}
        for group, tid in zip(result.groups, thread_ids, strict=True):
            for m in group:
                new = (tid, result.parent[m], result.method[m])
                if old[m] != new:
                    changes["ids"].append(m)
                    changes["threads"].append(new[0])
                    changes["parents"].append(new[1])
                    changes["methods"].append(new[2])
        if changes["ids"]:
            await s.execute(UPDATE_MESSAGE_THREADS, {"u": user_id, **changes})
        deleted = len(
            list(await s.scalars(DELETE_OTHER_THREADS, {"u": user_id, "keep": thread_ids}))
        )
        if overrides_version:
            await s.execute(MARK_OVERRIDES_APPLIED, {"u": user_id, "v": overrides_version})
    return ThreadStats(
        messages=len(rows),
        threads=len(thread_ids),
        changed=len(changes["ids"]),
        created=n_new,
        deleted=deleted,
    )


def _thread_columns() -> dict[str, list[Any]]:
    return {k: [] for k in ("ids", "counts", "firsts", "lasts")}


# --- một người dùng / việc tồn ---


async def process_user(
    engine: AsyncEngine, user_id: int, *, force_rethread: bool = False
) -> ProcessResult:
    started = time.monotonic()
    clean = await clean_pending(engine, user_id)
    threads = None
    if force_rethread or await needs_rethread(engine, user_id):
        threads = await rethread_user(engine, user_id)
    log.info(
        "process user=%s cleaned=%d clean_failed=%d messages=%s threads=%s changed=%s ms=%d",
        user_id,
        clean.cleaned,
        clean.failed,
        threads.messages if threads else "-",
        threads.threads if threads else "-",
        threads.changed if threads else "-",
        (time.monotonic() - started) * 1000,
    )
    return ProcessResult(clean, threads)


PENDING_USERS = text(
    """
    SELECT user_id FROM messages
    WHERE cleaner_version IS DISTINCT FROM :cv OR thread_id IS NULL
    UNION
    SELECT user_id FROM threads WHERE threader_version <> :tv
    UNION
    SELECT user_id FROM user_thread_state WHERE overrides_version > applied_overrides_version
    UNION
    SELECT coalesce(m.user_id, t.user_id)
    FROM (SELECT user_id, count(*) AS n FROM messages GROUP BY user_id) m
    FULL JOIN (SELECT user_id, sum(message_count) AS n FROM threads GROUP BY user_id) t
        ON t.user_id = m.user_id
    WHERE m.n IS DISTINCT FROM t.n
    ORDER BY 1
    """
)


async def pending_users(engine: AsyncEngine) -> list[int]:
    """Người dùng có việc tồn (spec worker mục 4)."""
    async with AsyncSession(engine) as s:
        return list(await s.scalars(PENDING_USERS, {"cv": CLEANER_VERSION, "tv": THREADER_VERSION}))


PENDING_OVERRIDE_USERS = text(
    "SELECT user_id FROM user_thread_state "
    "WHERE overrides_version > applied_overrides_version ORDER BY user_id"
)


async def pending_override_users(engine: AsyncEngine) -> list[int]:
    """Người dùng có sửa tay chưa áp dụng. Chỉ đọc bảng nhỏ user_thread_state (chạy mỗi 5 giây)."""
    async with AsyncSession(engine) as s:
        return list(await s.scalars(PENDING_OVERRIDE_USERS))
