"""Token của collector: lưu dạng hash, app_api chỉ xác thực qua hàm auth_token_user."""

import psycopg.errors
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from app.auth import hash_token
from app.cli import create_token, create_user, revoke_tokens


def auth(engine: Engine, token: str) -> int | None:
    with engine.begin() as conn:
        return conn.scalar(text("select auth_token_user(:h)"), {"h": hash_token(token)})


def test_token_resolves_to_user(owner_engine: Engine, api_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        user_id, token = create_user(conn, "PM@Example.com", "PM")
    assert token.startswith("pmm_") and len(token) > 40
    assert auth(api_engine, token) == user_id
    assert auth(api_engine, token + "x") is None
    with owner_engine.connect() as conn:
        email, last_used = conn.execute(
            text(
                "select u.email, t.last_used_at from api_tokens t join users u on u.id = t.user_id"
            )
        ).one()
    assert email == "pm@example.com"
    assert last_used is not None


def test_revoked_token_rejected(owner_engine: Engine, api_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        user_id, token = create_user(conn, "a@example.com", "A")
        second = create_token(conn, user_id, "máy 2")
    assert auth(api_engine, second) == user_id
    with owner_engine.begin() as conn:
        assert revoke_tokens(conn, user_id) == 2
    assert auth(api_engine, token) is None
    assert auth(api_engine, second) is None


def test_token_hash_stored_not_token(owner_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        _, token = create_user(conn, "a@example.com", "A")
        stored = conn.scalar(text("select token_hash from api_tokens"))
    assert bytes(stored) == hash_token(token)
    assert token.encode() not in bytes(stored)


def test_api_cannot_read_tokens(api_engine: Engine, migrated: None) -> None:
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        conn.execute(text("select token_hash from api_tokens"))
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)


def test_auth_function_only_for_api(owner_engine: Engine, migrated: None) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select r.rolname, has_function_privilege(r.rolname, "
                "'auth_token_user(bytea)', 'EXECUTE') as allowed "
                "from pg_roles r where r.rolname in ('app_api', 'app_worker')"
            )
        )
        allowed = {r.rolname: r.allowed for r in rows}
    assert allowed == {"app_api": True, "app_worker": False}
