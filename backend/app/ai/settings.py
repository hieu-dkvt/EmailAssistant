"""Cài đặt tên model theo tác vụ và thống kê lần gọi AI (spec 2026-10-19-ai-settings-design).

Chạy trong user_session (RLS): chỉ đọc/ghi cài đặt và thống kê của chính người dùng.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import TASKS

SELECT_MODELS = text("SELECT task, model FROM user_ai_settings")
UPSERT_MODEL = text(
    """
    INSERT INTO user_ai_settings (user_id, task, model) VALUES (:u, :task, :model)
    ON CONFLICT (user_id, task) DO UPDATE SET model = excluded.model, updated_at = now()
    """
)
DELETE_MODEL = text("DELETE FROM user_ai_settings WHERE user_id = :u AND task = :task")
USAGE = text(
    """
    SELECT model, count(*) AS calls, sum(input_tokens) AS input_tokens,
           sum(output_tokens) AS output_tokens, sum(cache_read_tokens) AS cache_read_tokens,
           sum(cache_write_tokens) AS cache_write_tokens
    FROM ai_calls
    WHERE created_at >= :since
    GROUP BY model ORDER BY model
    """
)


async def user_models(session: AsyncSession) -> dict[str, str]:
    """Tên model người dùng nhập theo tác vụ (spec 24); tác vụ chưa nhập thì không có mặt."""
    return {r.task: r.model for r in await session.execute(SELECT_MODELS) if r.task in TASKS}


async def save_models(
    session: AsyncSession, user_id: int, models: Mapping[str, str | None]
) -> None:
    """Có tên → ghi đè; None hoặc rỗng → xóa dòng của tác vụ (không còn dòng trống)."""
    for task, model in models.items():
        if model:
            await session.execute(UPSERT_MODEL, {"u": user_id, "task": task, "model": model})
        else:
            await session.execute(DELETE_MODEL, {"u": user_id, "task": task})


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def usage_since(session: AsyncSession, since: datetime) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for r in await session.execute(USAGE, {"since": since}):
        out.append(
            {
                "model": r.model,
                "calls": int(r.calls),
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cache_read_tokens": int(r.cache_read_tokens or 0),
                "cache_write_tokens": int(r.cache_write_tokens or 0),
            }
        )
    return out
