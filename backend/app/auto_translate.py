"""Job dịch tự động: chọn mail khách trong Inbox, dịch, ghi kết quả và chuyển folder trên web
(spec 26 mục 4, 5, 8).

Mọi đọc, ghi dữ liệu mail chạy trong user_session của engine role app_api (RLS): people,
glossary, call_task không lọc user_id trong câu truy vấn, role worker sẽ đọc lẫn mail người khác.
Riêng pending_translate_users chạy role app_worker: luôn lọc user_id, chỉ trả về id người dùng.
Không giữ transaction trong lúc chờ AI. Log chỉ có user, message_id, mã lỗi, ms.
"""

import logging
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.ai.client import LlmClient, LlmError
from app.ai.models import TASK_TRANSLATE
from app.ai.provider import LlmResolver
from app.ai.settings import user_models
from app.ai.translate import (
    TranslateError,
    TranslationResult,
    produce_translation,
    save_translation,
)
from app.db.session import user_session
from app.mail.domains import inbox_path_sql
from app.ml_client import MlClient, MlUnavailable
from app.safe_errors import describe

log = logging.getLogger("app.auto_translate")

MAX_MAIL_ATTEMPTS = 3
RETRY_WAIT_MINUTES = 10  # chờ attempts × ngần này phút
MAX_RETRY_WAIT_MINUTES = 60  # trần thời gian chờ
# Lỗi gắn với mail này: dừng ở mail này, mail sau vẫn chạy.
PER_MAIL_ERRORS = {"ai_bad_output", "ai_truncated", "ai_refused", "mask_unstable"}
# Có thể do một mail (quá dài) hoặc do cấu hình (sai tên model): mail chờ, mail sau vẫn chạy, nhưng
# không thành failed, nếu không sai tên model sẽ làm hỏng vĩnh viễn mọi mail đang chờ.
UNBOUNDED_RETRY_ERRORS = {"ai_bad_request"}
# Mọi LlmError khác là lỗi tạm: dừng người dùng này, không ghi gì.
RECORDED_ERRORS = PER_MAIL_ERRORS | UNBOUNDED_RETRY_ERRORS
NOTHING_TO_TRANSLATE = "nothing_to_translate"
MESSAGE_NOT_FOUND = "message_not_found"
# Lỗi không lường trước trong lúc xử lý một mail (Masker, kiểm tra kết quả): cùng mã với API.
UNEXPECTED_ERROR = "translate_failed"
# Hạ tầng hỏng không phải lỗi của mail: không tính lượt thử. Job báo lỗi, lần quét sau làm lại.
INFRASTRUCTURE_ERRORS = (
    OperationalError,
    InterfaceError,
    PoolTimeoutError,
    OSError,
    httpx.HTTPError,
)

_DOMAIN_MATCH = """
    strpos(m.from_addr, '@') > 0
    AND EXISTS (SELECT 1 FROM unnest(p.customer_domains) d
                WHERE d <> ''
                  AND (lower(split_part(m.from_addr, '@', -1)) = d
                       OR right(lower(split_part(m.from_addr, '@', -1)), length(d) + 1) = '.' || d))
"""
_INBOX_OWNERSHIP = f"""
    FROM message_ownership o
    JOIN mail_sources s ON s.id = o.source_id AND s.user_id = o.user_id
    WHERE o.user_id = m.user_id AND o.message_id = m.id AND NOT o.is_deleted
      AND {inbox_path_sql("s.folder_path")}
"""
# Điều kiện WHERE trên `messages m` (spec mục 4), tham số :u. Ghép được thêm AND.
ELIGIBLE_SQL = f"""
    m.user_id = :u
    AND m.body_clean IS NOT NULL
    AND m.thread_id IS NOT NULL
    AND EXISTS (
        SELECT 1 FROM message_projects mp
        JOIN projects p ON p.id = mp.project_id AND p.user_id = mp.user_id
        WHERE mp.user_id = m.user_id AND mp.message_id = m.id
          AND m.sent_at >= p.ai_since
          AND {_DOMAIN_MATCH}
    )
    AND EXISTS (SELECT 1 {_INBOX_OWNERSHIP})
    AND NOT EXISTS (
        SELECT 1 FROM message_auto_runs r
        WHERE r.user_id = m.user_id AND r.message_id = m.id
          AND (r.status IN ('done', 'failed')
               OR (r.status = 'retry' AND r.next_attempt_at > now()))
    )
"""
USERS_WITH_DOMAINS = text(
    "SELECT DISTINCT user_id FROM projects WHERE cardinality(customer_domains) > 0 ORDER BY user_id"
)
HAS_ELIGIBLE = text(f"SELECT EXISTS (SELECT 1 FROM messages m WHERE {ELIGIBLE_SQL})")
_CANDIDATE = f"""
    SELECT m.id, (SELECT min(o.source_id) {_INBOX_OWNERSHIP}) AS from_source_id
    FROM messages m
    WHERE {ELIGIBLE_SQL}
"""
NEXT_MESSAGE = text(f"{_CANDIDATE} ORDER BY m.sent_at, m.id LIMIT 1")
RECHECK = text(f"{_CANDIDATE} AND m.id = :m")
HAS_TRANSLATION = text(
    "SELECT EXISTS (SELECT 1 FROM message_translations WHERE user_id = :u AND message_id = :m)"
)
TARGET_FOLDERS = text(
    f"""
    SELECT DISTINCT p.processed_source_id
    FROM message_projects mp
    JOIN projects p ON p.id = mp.project_id AND p.user_id = mp.user_id
    JOIN messages m ON m.id = mp.message_id AND m.user_id = mp.user_id
    WHERE mp.user_id = :u AND mp.message_id = :m
      AND p.processed_source_id IS NOT NULL
      AND {_DOMAIN_MATCH}
    """
)
NO_FOLDER_RUNS = text(
    """
    SELECT r.message_id
    FROM message_auto_runs r
    JOIN message_projects mp ON mp.user_id = r.user_id AND mp.message_id = r.message_id
    WHERE r.user_id = :u AND mp.project_id = :p
      AND r.status = 'done' AND r.move_note = 'no_folder'
    """
)
SET_RUN_TARGET = text(
    "UPDATE message_auto_runs SET target_source_id = :t, move_note = :note, updated_at = now() "
    "WHERE user_id = :u AND message_id = :m AND status = 'done' AND move_note = 'no_folder'"
)
RUN_ATTEMPTS = text("SELECT attempts FROM message_auto_runs WHERE user_id = :u AND message_id = :m")
# Chỉ đi từ retry: dòng done/failed giữ nguyên (mail chuyển đúng một lần, kể cả khi job chạy đua).
UPSERT_RUN = text(
    """
    INSERT INTO message_auto_runs (user_id, message_id, status, attempts, next_attempt_at,
        error_code, from_source_id, target_source_id, move_note)
    VALUES (:u, :m, :status, :attempts, now() + make_interval(mins => CAST(:wait AS integer)),
        :error_code, :from_source, :target, :note)
    ON CONFLICT (user_id, message_id) DO UPDATE SET
        status = excluded.status,
        attempts = excluded.attempts,
        next_attempt_at = excluded.next_attempt_at,
        error_code = excluded.error_code,
        from_source_id = excluded.from_source_id,
        target_source_id = excluded.target_source_id,
        move_note = excluded.move_note,
        updated_at = now()
    WHERE message_auto_runs.status = 'retry'
    """
)


@dataclass(frozen=True)
class RunStats:
    handled: bool  # đã xử lý xong phần việc của một mail
    more: bool  # có thể còn mail chờ: job tự đẩy tiếp


async def pending_translate_users(engine: AsyncEngine) -> list[int]:
    """Người dùng có mail đủ tiêu chí. Role worker (bỏ qua RLS): mỗi truy vấn lọc đúng một người,
    không đọc nội dung mail."""
    async with AsyncSession(engine) as s:
        users = list(await s.scalars(USERS_WITH_DOMAINS))
        return [u for u in users if await s.scalar(HAS_ELIGIBLE, {"u": u})]


async def next_message(session: AsyncSession, user_id: int) -> tuple[int, int] | None:
    """(message_id, from_source_id) của mail đủ tiêu chí cũ nhất."""
    row = (await session.execute(NEXT_MESSAGE, {"u": user_id})).first()
    return None if row is None else (row.id, row.from_source_id)


async def choose_target(
    session: AsyncSession, user_id: int, message_id: int
) -> tuple[int | None, str | None]:
    """(target_source_id, move_note) theo các dự án của mail có domain khớp (spec mục 8)."""
    params = {"u": user_id, "m": message_id}
    folders = list(await session.scalars(TARGET_FOLDERS, params))
    if not folders:
        return None, "no_folder"
    if len(folders) > 1:
        return None, "ambiguous"  # không tự chọn (nguyên tắc spec 25), web hỏi người dùng
    return folders[0], None


async def retarget_no_folder(session: AsyncSession, user_id: int, project_id: int) -> None:
    """Dự án vừa được chọn folder: xét lại mail của dự án đã xong mà chưa có folder (spec mục 8).

    Thứ tự tự nhiên là lưu domain, đồng bộ folder rồi mới chọn folder, job đã chạy xong từ trước.
    Chạy trong transaction lưu dự án. move-back xóa note nên chuyển lại có chủ ý không bị đảo.
    """
    params = {"u": user_id, "p": project_id}
    for message_id in list(await session.scalars(NO_FOLDER_RUNS, params)):
        target, note = await choose_target(session, user_id, message_id)
        if note != "no_folder":
            await session.execute(
                SET_RUN_TARGET, {"t": target, "note": note, "u": user_id, "m": message_id}
            )


async def _next_attempt(session: AsyncSession, user_id: int, message_id: int) -> int:
    done = await session.scalar(RUN_ATTEMPTS, {"u": user_id, "m": message_id})
    return (done or 0) + 1


async def _write_run(
    session: AsyncSession,
    user_id: int,
    message_id: int,
    *,
    status: str,
    attempts: int,
    from_source: int | None,
    error_code: str | None = None,
    wait_minutes: int | None = None,
    target: int | None = None,
    note: str | None = None,
) -> None:
    await session.execute(
        UPSERT_RUN,
        {
            "u": user_id,
            "m": message_id,
            "status": status,
            "attempts": attempts,
            "wait": wait_minutes,
            "error_code": error_code,
            "from_source": from_source,
            "target": target,
            "note": note,
        },
    )


def _is_fk_violation(e: IntegrityError) -> bool:
    return getattr(e.orig, "sqlstate", None) == "23503"


async def _finish(
    engine: AsyncEngine,
    user_id: int,
    message_id: int,
    result: TranslationResult | None,
    error_code: str | None,
) -> RunStats:
    """Một transaction: kiểm tra lại tiêu chí, ghi bản dịch, ghi dòng run (tức là chuyển folder).

    Mail hết đủ tiêu chí hoặc bị xóa trong lúc chờ AI thì không ghi gì, sang mail khác."""
    try:
        async with user_session(engine, user_id) as s:
            row = (await s.execute(RECHECK, {"u": user_id, "m": message_id})).first()
            if row is None:
                return RunStats(handled=True, more=True)
            if result is not None:
                await save_translation(s, user_id, message_id, result)
            target, note = await choose_target(s, user_id, message_id)
            await _write_run(
                s,
                user_id,
                message_id,
                status="done",
                attempts=await _next_attempt(s, user_id, message_id),
                from_source=row.from_source_id,
                error_code=error_code,
                target=target,
                note=note,
            )
            return RunStats(handled=True, more=await next_message(s, user_id) is not None)
    except IntegrityError as e:
        if not _is_fk_violation(e):
            raise
        return RunStats(handled=True, more=True)


async def _record_failure(
    engine: AsyncEngine, user_id: int, message_id: int, from_source: int, code: str
) -> RunStats:
    """Chờ attempts × 10 phút (tối đa 60), hết lượt thì failed, mail ở lại Inbox.

    UNBOUNDED_RETRY_ERRORS không bao giờ thành failed."""
    try:
        async with user_session(engine, user_id) as s:
            attempt = await _next_attempt(s, user_id, message_id)
            failed = code not in UNBOUNDED_RETRY_ERRORS and attempt >= MAX_MAIL_ATTEMPTS
            wait = min(attempt * RETRY_WAIT_MINUTES, MAX_RETRY_WAIT_MINUTES)
            await _write_run(
                s,
                user_id,
                message_id,
                status="failed" if failed else "retry",
                attempts=attempt,
                from_source=from_source,
                error_code=code,
                wait_minutes=None if failed else wait,
            )
    except IntegrityError as e:
        if not _is_fk_violation(e):
            raise
    return RunStats(handled=True, more=True)


async def _produce(
    engine: AsyncEngine, ml: MlClient, llm: LlmClient, user_id: int, message_id: int, custom: bool
) -> tuple[TranslationResult | None, str | None]:
    """(bản dịch, error_code). Mail rỗng không gọi AI, vẫn đi tiếp để ghi done và chuyển folder."""
    try:
        result = await produce_translation(
            engine, ml, llm, user_id, message_id, custom_provider=custom
        )
    except TranslateError as e:
        if e.code != NOTHING_TO_TRANSLATE:
            raise
        return None, e.code
    return result, None


async def translate_next(
    api_engine: AsyncEngine, ml: MlClient, resolver: LlmResolver, user_id: int
) -> RunStats:
    """Xử lý đúng một mail, cũ nhất trước. Lỗi tạm: dừng người dùng này, không ghi gì, chờ quét."""
    started = time.monotonic()
    stop = RunStats(handled=False, more=False)
    try:
        llm, custom = await resolver.for_user(api_engine, user_id)
    except LlmError as e:
        log.info("auto_translate user=%s stopped code=%s", user_id, e.code)
        return stop
    if llm is None:
        return stop
    async with user_session(api_engine, user_id) as s:
        if custom and TASK_TRANSLATE not in await user_models(s):
            return stop
        picked = await next_message(s, user_id)
        if picked is None:
            return stop
        message_id, from_source = picked
        translated = bool(await s.scalar(HAS_TRANSLATION, {"u": user_id, "m": message_id}))

    code: str
    try:
        result, error_code = (
            (None, None)
            if translated
            else await _produce(api_engine, ml, llm, user_id, message_id, custom)
        )
        stats = await _finish(api_engine, user_id, message_id, result, error_code)
    except (TranslateError, LlmError) as e:
        if e.code == MESSAGE_NOT_FOUND:
            return RunStats(handled=True, more=True)
        if e.code not in RECORDED_ERRORS:
            log.info(
                "auto_translate user=%s message_id=%s stopped code=%s", user_id, message_id, e.code
            )
            return stop
        code = e.code
    except MlUnavailable:
        log.info("auto_translate user=%s stopped code=ml_unavailable", user_id)
        return stop
    except INFRASTRUCTURE_ERRORS:
        raise
    except Exception as e:
        # Lỗi lạ (Masker, thay ngược, ghi kết quả) không được làm job lỗi mãi ở mail cũ nhất, chặn
        # các mail sau. Có giới hạn lượt thử, người dùng thấy mã lỗi. Thông điệp lỗi có thể chứa
        # nội dung mail: chỉ log describe().
        log.error(
            "auto_translate user=%s message_id=%s unexpected %s", user_id, message_id, describe(e)
        )
        code = UNEXPECTED_ERROR
    else:
        log.info(
            "auto_translate user=%s message_id=%s done ms=%d",
            user_id,
            message_id,
            (time.monotonic() - started) * 1000,
        )
        return stats
    log.info(
        "auto_translate user=%s message_id=%s failed code=%s ms=%d",
        user_id,
        message_id,
        code,
        (time.monotonic() - started) * 1000,
    )
    return await _record_failure(api_engine, user_id, message_id, from_source, code)
