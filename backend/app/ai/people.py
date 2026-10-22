"""Từ điển người gửi/nhận của người hỏi để che tên (spec 20 mục 4.2).

Đọc header mail trong user_session (RLS): chỉ người mà người hỏi từng gửi/nhận mail. Cache trong
bộ nhớ process theo user_id, hết hạn sau CACHE_SECONDS. Không log tên, địa chỉ.
"""

import time
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.ai.names import Entity, companies_in, entities_from_display

MAX_PEOPLE = 3_000
CACHE_SECONDS = 600.0

CORRESPONDENTS = text(
    """
    SELECT name, addr, count(*) AS n FROM (
        SELECT m.from_name AS name, lower(m.from_addr) AS addr FROM messages m
        UNION ALL
        SELECT e ->> 'name', lower(e ->> 'addr')
        FROM messages m, jsonb_array_elements(m.to_addrs || m.cc_addrs) e
    ) x
    WHERE name IS NOT NULL OR addr IS NOT NULL
    GROUP BY name, addr
    ORDER BY n DESC, addr, name
    LIMIT :limit
    """
)

# Tên công ty trong toàn văn (cả chữ ký) của các mail gần nhất: chữ ký bị cắt khỏi body_clean nhưng
# tên viết tắt của khách vẫn có trong subject/nội dung (【サンプルA】).
RECENT_BODIES = text(
    "SELECT left(m.body_text, :cap) FROM messages m "
    "ORDER BY m.sent_at DESC NULLS LAST, m.id DESC LIMIT :limit"
)
RECENT_MAILS = 500
BODY_CAP = 20_000

_cache: dict[int, tuple[float, list[Entity]]] = {}


def entities_of(people: Iterable[tuple[str | None, str | None]]) -> list[Entity]:
    out: list[Entity] = []
    for name, addr in people:
        out += entities_from_display(name, addr)
    return out


async def correspondents(session: AsyncSession, user_id: int) -> list[Entity]:
    now = time.monotonic()
    # Bỏ mục hết hạn của mọi người dùng: tên, địa chỉ không nằm lại trong RAM quá lâu.
    for uid in [u for u, (at, _) in _cache.items() if now - at >= CACHE_SECONDS]:
        del _cache[uid]
    hit = _cache.get(user_id)
    if hit is not None:
        return hit[1]
    people = [
        (r.name, r.addr) for r in await session.execute(CORRESPONDENTS, {"limit": MAX_PEOPLE})
    ]
    bodies = [
        b
        for b in await session.scalars(RECENT_BODIES, {"cap": BODY_CAP, "limit": RECENT_MAILS})
        if b
    ]

    def build() -> list[Entity]:
        return entities_of(people) + companies_in(bodies)

    # Regex trên tới ~10 triệu ký tự: chạy ở thread riêng, không chặn event loop của API.
    entities = await run_in_threadpool(build)
    _cache[user_id] = (now, entities)
    return entities


def clear_cache() -> None:
    _cache.clear()
