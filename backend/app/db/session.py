"""Phiên DB cho code phục vụ người dùng.

Mọi truy vấn phục vụ request của người dùng phải đi qua user_session: phiên chạy trong một
transaction đã đặt app.user_id (set_config(..., true), tương đương SET LOCAL), nên Row-Level
Security chỉ cho thấy dữ liệu của người đó và giá trị không dính sang request sau.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.config import database_url

SET_USER = text("select set_config('app.user_id', :uid, true)")


def create_api_engine(url: str | None = None, **kwargs: Any) -> AsyncEngine:
    """Engine async bằng role app_api (luôn chịu RLS).

    hide_parameters: tham số bind (có thể là nội dung mail) không nằm trong text của exception,
    nên log exception không làm lộ mail.
    """
    return create_async_engine(
        url or database_url("api"), pool_pre_ping=True, hide_parameters=True, **kwargs
    )


def _check_user_id(user_id: int) -> None:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError(f"user_id không hợp lệ: {user_id!r}")


@asynccontextmanager
async def user_session(engine: AsyncEngine, user_id: int) -> AsyncIterator[AsyncSession]:
    """Session trong transaction của user_id: commit khi thoát bình thường, rollback khi lỗi."""
    _check_user_id(user_id)
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        await session.execute(SET_USER, {"uid": str(user_id)})
        yield session


@contextmanager
def user_connection(engine: Engine, user_id: int) -> Iterator[Connection]:
    """Bản sync của user_session cho CLI: engine role app_api, transaction đã đặt app.user_id."""
    _check_user_id(user_id)
    with engine.begin() as conn:
        conn.execute(SET_USER, {"uid": str(user_id)})
        yield conn
