"""Cài đặt cấp model theo tác vụ và thống kê lần gọi AI (spec 2026-10-19-ai-settings-design).

Chạy trong user_session (RLS): chỉ đọc/ghi cài đặt và thống kê của chính người dùng.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import TASKS, TIERS, Tier, estimate_cost

SELECT_TIERS = text("SELECT task, tier FROM user_ai_settings")
SELECT_MODELS = text("SELECT task, model FROM user_ai_settings WHERE model IS NOT NULL")
UPSERT_MODEL = text(
    """
    INSERT INTO user_ai_settings (user_id, task, model) VALUES (:u, :task, :model)
    ON CONFLICT (user_id, task) DO UPDATE SET model = excluded.model, updated_at = now()
    """
)
UPSERT_TIER = text(
    """
    INSERT INTO user_ai_settings (user_id, task, tier) VALUES (:u, :task, :tier)
    ON CONFLICT (user_id, task) DO UPDATE SET tier = excluded.tier, updated_at = now()
    """
)
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


async def user_tiers(session: AsyncSession) -> dict[str, Tier]:
    """Cấp của mọi tác vụ: cài đặt của người dùng, chưa chọn thì cấp mặc định."""
    chosen = {r.task: r.tier for r in await session.execute(SELECT_TIERS)}
    return {
        name: (chosen[name] if chosen.get(name) in TIERS else task.default_tier)
        for name, task in TASKS.items()
    }


async def save_tiers(session: AsyncSession, user_id: int, tiers: Mapping[str, Tier]) -> None:
    for task, tier in tiers.items():
        await session.execute(UPSERT_TIER, {"u": user_id, "task": task, "tier": tier})


async def user_models(session: AsyncSession) -> dict[str, str]:
    """Tên model người dùng nhập theo tác vụ (spec 24); tác vụ chưa nhập thì không có mặt."""
    return {r.task: r.model for r in await session.execute(SELECT_MODELS) if r.task in TASKS}


async def save_models(
    session: AsyncSession, user_id: int, models: Mapping[str, str | None]
) -> None:
    for task, model in models.items():
        await session.execute(UPSERT_MODEL, {"u": user_id, "task": task, "model": model or None})


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
                "estimated_usd": estimate_cost(
                    r.model,
                    int(r.input_tokens or 0),
                    int(r.output_tokens or 0),
                    int(r.cache_read_tokens or 0),
                    int(r.cache_write_tokens or 0),
                ),
            }
        )
    return out
