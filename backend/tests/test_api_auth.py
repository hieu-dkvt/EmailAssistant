"""API: xác thực bằng token collector, /api/v1/me, lỗi 422 không lộ dữ liệu gửi lên."""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine

from app.cli import create_user, revoke_tokens
from app.db.session import create_api_engine
from app.main import create_app
from tests.dbutil import url_for_test_db


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_api_engine(url_for_test_db("api"))
    app = create_app(engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest.fixture
def users(owner_engine: Engine, clean: None) -> SimpleNamespace:
    with owner_engine.begin() as conn:
        a, token_a = create_user(conn, "a@example.com", "Người A")
        b, token_b = create_user(conn, "b@example.com", "Người B")
    return SimpleNamespace(a=a, b=b, token_a=token_a, token_b=token_b)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_me_returns_token_owner(client: AsyncClient, users: SimpleNamespace) -> None:
    r = await client.get("/api/v1/me", headers=bearer(users.token_a))
    assert r.status_code == 200
    assert r.json() == {"id": users.a, "email": "a@example.com", "display_name": "Người A"}
    r = await client.get("/api/v1/me", headers=bearer(users.token_b))
    assert r.json()["id"] == users.b


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer pmm_wrong"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
    ],
)
async def test_bad_token_401(
    client: AsyncClient, users: SimpleNamespace, headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/me", headers=headers)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
    assert r.json() == {"detail": "Token không hợp lệ"}


async def test_revoked_token_401(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        revoke_tokens(conn, users.a)
    r = await client.get("/api/v1/me", headers=bearer(users.token_a))
    assert r.status_code == 401


async def test_validation_error_does_not_echo_input(
    client: AsyncClient, users: SimpleNamespace
) -> None:
    secret = "NỘI-DUNG-MAIL-BÍ-MẬT-123"
    r = await client.post(
        "/api/v1/ingest",
        headers=bearer(users.token_a),
        json={"schema_version": 1, "unexpected": secret},
    )
    assert r.status_code == 422
    assert secret not in r.text
    assert all(set(e) == {"loc", "msg", "type"} for e in r.json()["detail"])
