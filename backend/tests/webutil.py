"""Tiện ích test cho API của web: client có engine DB test, người dùng có mật khẩu, đăng nhập."""

from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app.cli import create_user, set_password
from app.main import create_app

CSRF = {"X-PMM-Request": "1"}
PASSWORD = "mat-khau-thu-nghiem-12"


def web_client(engine: AsyncEngine) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app(engine)), base_url="http://test")


def add_web_user(owner_engine: Engine, email: str, password: str | None = PASSWORD) -> int:
    """Người dùng (kèm token collector) và mật khẩu web; password=None: chưa đặt mật khẩu."""
    with owner_engine.begin() as conn:
        user_id, _ = create_user(conn, email, email.split("@")[0])
        if password is not None:
            set_password(conn, user_id, password)
    return user_id


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> None:
    r = await client.post(
        "/api/v1/session", json={"email": email, "password": password}, headers=CSRF
    )
    assert r.status_code == 200, r.text
