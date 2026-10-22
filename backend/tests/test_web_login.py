"""Đăng nhập web: mật khẩu, phiên cookie, chống CSRF, giới hạn sai (spec 13)."""

import asyncio
import io
import logging
import unicodedata
from collections.abc import AsyncIterator

import psycopg.errors
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import ProgrammingError

from app import cli
from app.cli import create_token
from app.db.session import create_api_engine
from app.main import create_app
from app.run import ACCESS_LOGGER, DropQueryString, make_server
from app.web_auth import (
    SESSION_COOKIE,
    LoginThrottle,
    hash_password,
    password_problem,
    verify_password,
)
from tests.dbutil import url_for_test_db
from tests.test_api_ingest import bearer
from tests.test_review import use_test_db
from tests.webutil import CSRF, PASSWORD, add_web_user, login

EMAIL = "pm@vn.example.com"


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    engine = create_api_engine(url_for_test_db("api"))
    yield create_app(engine)
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def user(owner_engine: Engine, clean: None) -> int:
    return add_web_user(owner_engine, EMAIL)


def test_password_hash() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert verify_password("correct horse battery", h)
    assert not verify_password("correct horse batterx", h)
    assert hash_password("correct horse battery") != h  # salt
    for broken in ("", "scrypt$1$2", "bcrypt$x$y$z$a$b", "scrypt$a$8$1$xx$yy", h + "!"):
        assert not verify_password("correct horse battery", broken)
    # Tham số lớn hơn mức của app (vd. chuỗi băm bị sửa) → sai, không tốn bộ nhớ.
    scheme, _, r, p, salt, key = h.split("$")
    assert not verify_password(
        "correct horse battery", "$".join([scheme, str(2**20), r, p, salt, key])
    )


async def test_login_sets_session_cookie(client: AsyncClient, user: int) -> None:
    r = await client.post(
        "/api/v1/session", json={"email": " PM@vn.example.com ", "password": PASSWORD}, headers=CSRF
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"id": user, "email": EMAIL, "display_name": "pm"}
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith(f"{SESSION_COOKIE}=pmw_")
    for attr in ("httponly", "samesite=strict", "path=/api", "max-age=43200"):
        assert attr in cookie
    assert "secure" not in cookie  # HTTP trên máy dev
    r = await client.get("/api/v1/session")
    assert r.status_code == 200 and r.json()["id"] == user


async def test_login_failures_look_the_same(
    client: AsyncClient, owner_engine: Engine, user: int
) -> None:
    add_web_user(owner_engine, "nopass@vn.example.com", password=None)
    for email, password in (
        (EMAIL, PASSWORD + "x"),
        ("unknown@vn.example.com", PASSWORD),
        ("nopass@vn.example.com", PASSWORD),
    ):
        r = await client.post(
            "/api/v1/session", json={"email": email, "password": password}, headers=CSRF
        )
        assert r.status_code == 401
        assert r.json() == {
            "detail": "Email hoặc mật khẩu không đúng",
            "code": "invalid_credentials",
        }
        assert "set-cookie" not in r.headers
    assert (await client.get("/api/v1/session")).status_code == 401


async def test_csrf_header_required(client: AsyncClient, user: int) -> None:
    r = await client.post("/api/v1/session", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403 and r.json()["code"] == "csrf_header_missing"
    await login(client, EMAIL)
    r = await client.delete("/api/v1/session")
    assert r.status_code == 403
    assert (await client.get("/api/v1/session")).status_code == 200


async def test_logout_revokes_session(client: AsyncClient, user: int) -> None:
    await login(client, EMAIL)
    token = client.cookies[SESSION_COOKIE]
    r = await client.delete("/api/v1/session", headers=CSRF)
    assert r.status_code == 204
    assert SESSION_COOKIE not in client.cookies
    # Cookie cũ (vd. bị chép trước khi đăng xuất) không dùng được nữa.
    client.cookies.set(SESSION_COOKIE, token, domain="test", path="/api")
    r = await client.get("/api/v1/session")
    assert r.status_code == 401 and r.json()["code"] == "not_authenticated"
    # Không có phiên vẫn 204.
    client.cookies.clear()
    assert (await client.delete("/api/v1/session", headers=CSRF)).status_code == 204


async def test_expired_session_rejected(
    client: AsyncClient, owner_engine: Engine, user: int
) -> None:
    await login(client, EMAIL)
    with owner_engine.begin() as conn:
        conn.execute(text("update web_sessions set expires_at = now() - interval '1 second'"))
    assert (await client.get("/api/v1/session")).status_code == 401


async def test_set_password_revokes_sessions(
    client: AsyncClient, owner_engine: Engine, user: int
) -> None:
    await login(client, EMAIL)
    with owner_engine.begin() as conn:
        assert cli.set_password(conn, user, "mat-khau-moi-123456") == 1
    assert (await client.get("/api/v1/session")).status_code == 401
    r = await client.post(
        "/api/v1/session", json={"email": EMAIL, "password": PASSWORD}, headers=CSRF
    )
    assert r.status_code == 401
    await login(client, EMAIL, "mat-khau-moi-123456")


async def test_login_throttle(app: FastAPI, client: AsyncClient, user: int) -> None:
    now = [1000.0]
    app.state.login_throttle = LoginThrottle(clock=lambda: now[0])
    for _ in range(5):
        r = await client.post(
            "/api/v1/session", json={"email": EMAIL, "password": "sai"}, headers=CSRF
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/v1/session", json={"email": EMAIL, "password": PASSWORD}, headers=CSRF
    )
    assert r.status_code == 429 and r.json()["code"] == "too_many_attempts"
    now[0] += 15 * 60
    await login(client, EMAIL)
    # Đăng nhập đúng xóa bộ đếm.
    assert app.state.login_throttle.blocked(EMAIL) is False


def test_throttle_counts_per_email_and_in_flight() -> None:
    now = [0.0]
    t = LoginThrottle(max_failures=2, window=10, clock=lambda: now[0])
    for email in ("a", "a", "b"):
        assert t.begin(email)
        t.finish(email, ok=False)
    assert t.blocked("a") and not t.blocked("b")
    assert not t.begin("a")
    now[0] = 10
    assert not t.blocked("a")
    # Lần thử đang chạy được tính: lần thứ 3 bị từ chối trước khi hai lần đầu xong.
    assert t.begin("c") and t.begin("c") and not t.begin("c")
    t.finish("c", ok=True)
    t.finish("c", ok=True)
    assert "c" not in t._entries  # đúng thì xóa bộ đếm


async def test_concurrent_attempts_cannot_bypass_throttle(client: AsyncClient, user: int) -> None:
    async def attempt() -> int:
        r = await client.post(
            "/api/v1/session", json={"email": EMAIL, "password": "sai-mat-khau"}, headers=CSRF
        )
        return r.status_code

    codes = await asyncio.gather(*(attempt() for _ in range(20)))
    assert codes.count(401) == 5 and codes.count(429) == 15


async def test_token_and_session_are_separate(
    client: AsyncClient, owner_engine: Engine, user: int
) -> None:
    with owner_engine.begin() as conn:
        token = create_token(conn, user)
    # Token collector không mở được API của web.
    r = await client.get("/api/v1/session", headers=bearer(token))
    assert r.status_code == 401
    # Phiên web không gọi được API của collector.
    await login(client, EMAIL)
    assert (await client.get("/api/v1/sources")).status_code == 401
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_validation_error_hides_password(client: AsyncClient, user: int) -> None:
    secret = "bi-mat-khong-duoc-tra-lai"
    r = await client.post(
        "/api/v1/session",
        json={"email": EMAIL, "password": secret, "thua": secret},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert secret not in r.text


@pytest.mark.parametrize("table", ["user_passwords", "web_sessions"])
@pytest.mark.parametrize("role", ["api", "worker"])
def test_roles_cannot_read_auth_tables(
    owner_engine: Engine, migrated: None, table: str, role: str
) -> None:
    engine = create_engine(url_for_test_db("api" if role == "api" else "worker"))
    try:
        with pytest.raises(ProgrammingError) as ei, engine.begin() as conn:
            conn.execute(text(f"select * from {table}"))
        assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    finally:
        engine.dispose()


def test_session_functions_only_for_api(owner_engine: Engine, migrated: None) -> None:
    functions = (
        "web_password_hash(text)",
        "web_session_start(bigint, bytea, timestamptz)",
        "web_session_user(bytea)",
        "web_session_end(bytea)",
    )
    with owner_engine.connect() as conn:
        for fn in functions:
            rows = conn.execute(
                text(
                    "select r.rolname, has_function_privilege(r.rolname, :fn, 'EXECUTE') as ok "
                    "from pg_roles r where r.rolname in ('app_api', 'app_worker')"
                ),
                {"fn": fn},
            )
            assert {r.rolname: r.ok for r in rows} == {"app_api": True, "app_worker": False}, fn


def test_security_definer_functions_have_safe_search_path(
    owner_engine: Engine, migrated: None
) -> None:
    # pg_temp phải đứng cuối: nếu không, PostgreSQL tra kiểu/bảng trong pg_temp trước pg_catalog.
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select p.proname, p.proconfig from pg_proc p "
                "join pg_namespace n on n.oid = p.pronamespace "
                "where p.prosecdef and n.nspname = 'public'"
            )
        ).all()
        temp = {
            role: conn.scalar(
                text("select has_database_privilege(:r, current_database(), 'TEMPORARY')"),
                {"r": role},
            )
            for role in ("app_api", "app_worker")
        }
    assert {r.proname for r in rows} >= {"auth_token_user", "web_session_user"}
    for r in rows:
        assert r.proconfig == ["search_path=pg_catalog, pg_temp"], r.proname
    assert temp == {"app_api": False, "app_worker": False}


def test_access_log_drops_query_string() -> None:
    make_server("127.0.0.1", 8999)
    access = logging.getLogger(ACCESS_LOGGER)
    assert any(isinstance(f, DropQueryString) for f in access.filters)
    record = logging.LogRecord(
        ACCESS_LOGGER,
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/v1/search?q=%E8%A6%8B%E7%A9%8D&sender=x%40y", "1.1", 200),
        None,
    )
    for f in access.filters:
        if isinstance(f, logging.Filter):
            f.filter(record)
    message = record.getMessage()
    assert message == '127.0.0.1:5000 - "GET /api/v1/search HTTP/1.1" 200'


def test_cli_set_password(
    owner_engine: Engine,
    user: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_test_db(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("ngan\n"))
    with pytest.raises(SystemExit) as ei:
        cli.main(["set-password", "--email", EMAIL, "--password-stdin"])
    assert ei.value.code == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("mat-khau-dai-du-roi\n"))
    assert cli.main(["set-password", "--email", EMAIL, "--password-stdin"]) == 0
    out = capsys.readouterr().out
    assert "mat-khau" not in out
    assert verify_password("mat-khau-dai-du-roi", stored_hash(owner_engine, user))


def stored_hash(owner_engine: Engine, user_id: int) -> str:
    with owner_engine.connect() as conn:
        return conn.scalar(
            text("select password_hash from user_passwords where user_id = :u"), {"u": user_id}
        )


def piped_stdin(data: bytes) -> io.TextIOWrapper:
    """stdin như khi pipe từ shell: có buffer byte."""
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8")


def test_cli_set_password_from_powershell_pipe(
    owner_engine: Engine, user: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PowerShell pipe chuỗi kèm BOM và CRLF: trước đây BOM bị lưu vào mật khẩu, đăng nhập luôn sai.
    use_test_db(monkeypatch)
    password = "mật-khẩu-thử-1234"
    monkeypatch.setattr("sys.stdin", piped_stdin(b"\xef\xbb\xbf" + password.encode() + b"\r\n"))
    assert cli.main(["set-password", "--email", EMAIL, "--password-stdin"]) == 0
    assert verify_password(password, stored_hash(owner_engine, user))
    monkeypatch.setattr("sys.stdin", piped_stdin(b"mat-khau-\xff\xfe-dai\n"))
    with pytest.raises(SystemExit) as ei:
        cli.main(["set-password", "--email", EMAIL, "--password-stdin"])
    assert ei.value.code == 2


def test_cli_set_password_interactive(
    owner_engine: Engine,
    user: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_test_db(monkeypatch)
    answers = iter(["mat-khau-dai-du-roi", "mat-khau-dai-du-roj"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit) as ei:
        cli.main(["set-password", "--email", EMAIL])
    assert ei.value.code == 2 and "không khớp" in capsys.readouterr().err
    answers = iter(["mat-khau-dai-du-roi"] * 2)
    assert cli.main(["set-password", "--email", " PM@vn.example.com "]) == 0
    assert verify_password("mat-khau-dai-du-roi", stored_hash(owner_engine, user))


def test_cli_set_password_unknown_email_asks_nothing(
    owner_engine: Engine, user: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_test_db(monkeypatch)

    def no_prompt(prompt: str = "") -> str:
        raise AssertionError("không được hỏi mật khẩu khi email không có")

    monkeypatch.setattr("getpass.getpass", no_prompt)
    with pytest.raises(SystemExit) as ei:
        cli.main(["set-password", "--email", "khong-co@vn.example.com"])
    assert "Không có người dùng" in str(ei.value.code)


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("mat-khau-dai-du-roi", True),
        ("mật khẩu có dấu cách", True),
        ("ngan-qua", False),
        ("﻿mat-khau-dai-du-roi", False),
        ("mat-khau-�-dai-du", False),
        ("mat-khau\tdai-du-roi", False),
        ("mat-khau-​dai-du-roi", False),
    ],
)
def test_password_problem(password: str, ok: bool) -> None:
    assert (password_problem(password) is None) == ok


def test_password_unicode_normalized() -> None:
    # Gõ tiếng Việt dạng tổ hợp (NFD) hay dựng sẵn (NFC) đều đăng nhập được.
    nfc = unicodedata.normalize("NFC", "mật-khẩu-thử-1234")
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    assert verify_password(nfc, hash_password(nfd))
    assert verify_password(nfd, hash_password(nfc))
