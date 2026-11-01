import asyncio
import sys
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import web_auth
from app.processing import create_worker_engine
from tests.dbutil import (
    add_message,
    add_ownership,
    add_source,
    add_user,
    run_alembic,
    url_for_test_db,
)

MAIL_TABLES = (
    "users",
    "mail_sources",
    "messages",
    "message_ownership",
    "threads",
    "thread_overrides",
    "user_thread_state",
    "user_passwords",
    "web_sessions",
    "message_chunks",
    "user_ai_settings",
    "user_ai_provider",
    "ai_calls",
    "projects",
    "message_projects",
    "user_project_state",
    "glossary_terms",
    "message_translations",
    "message_auto_runs",
)

# psycopg async không chạy được trên ProactorEventLoop (mặc định của Windows).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """scrypt của app (n = 2^15) mất khoảng 0,6 giây mỗi lần: test dùng n nhỏ cho nhanh.

    verify_password nhận mọi n ≤ SCRYPT_N, nên logic băm/kiểm tra vẫn được test đầy đủ.
    """
    monkeypatch.setattr(web_auth, "SCRYPT_N", 2**10)


@pytest.fixture(autouse=True)
def allow_test_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client test dùng base_url http://test: cho phép Host "test" (app chỉ nhận máy cục bộ)."""
    monkeypatch.setenv("PMM_ALLOWED_HOSTS", "127.0.0.1,localhost,test")


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(url_for_test_db("owner"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def api_engine() -> Iterator[Engine]:
    engine = create_engine(url_for_test_db("api"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def worker_engine() -> Iterator[Engine]:
    engine = create_engine(url_for_test_db("worker"))
    yield engine
    engine.dispose()


@pytest.fixture
async def worker() -> AsyncIterator[AsyncEngine]:
    """Engine async role app_worker (như worker thật) trên DB test."""
    engine = create_worker_engine(url_for_test_db("worker"))
    yield engine
    await engine.dispose()


def truncate_mail_tables(engine: Engine) -> None:
    """Xóa dữ liệu của các bảng mail đang tồn tại (DB có thể đang ở revision cũ)."""
    with engine.begin() as conn:
        existing = [
            t for t in MAIL_TABLES if conn.scalar(text(f"select to_regclass('public.{t}')"))
        ]
        if existing:
            conn.execute(text(f"TRUNCATE {', '.join(existing)} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine) -> None:
    """Đưa database test về trạng thái sạch rồi migrate lên head, một lần mỗi phiên test.

    Xóa dữ liệu sót từ lần chạy trước trước khi downgrade: migration 0004 không downgrade
    được khi hai người dùng cùng có một message_id_hash.
    """
    truncate_mail_tables(owner_engine)
    run_alembic("downgrade", "base")
    run_alembic("upgrade", "head")


@pytest.fixture
def clean(owner_engine: Engine, migrated: None) -> None:
    """Xóa dữ liệu của các bảng mail trước mỗi test."""
    truncate_mail_tables(owner_engine)


@pytest.fixture
def seeded(owner_engine: Engine, clean: None) -> SimpleNamespace:
    """Hai người dùng A, B. Mỗi người có bản messages riêng (không dùng chung giữa người dùng).

    Folder: sA, sA2 của A; sB của B.
    Mail: m1 của A; m2 của B; cùng một mail (hash "h3") có hai bản riêng: m3a của A (ở sA)
    và m3b của B (ở sB); m4 của A nằm ở hai folder sA và sA2 (một bản, hai ownership).
    """
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        s_a = add_source(conn, a, "ImapMail/imap.example.com/INBOX")
        s_a2 = add_source(
            conn, a, "Mail/Local Folders/Inbox", kind="local", account="Local Folders"
        )
        s_b = add_source(conn, b, "ImapMail/imap.example.com/INBOX")
        m1 = add_message(conn, "h1", user_id=a)
        m2 = add_message(conn, "h2", user_id=b)
        m3a = add_message(conn, "h3", user_id=a)
        m3b = add_message(conn, "h3", user_id=b)
        m4 = add_message(conn, "h4", user_id=a)
        for m, u, s in (
            (m1, a, s_a),
            (m3a, a, s_a),
            (m4, a, s_a),
            (m4, a, s_a2),
            (m2, b, s_b),
            (m3b, b, s_b),
        ):
            add_ownership(conn, m, u, s)
    return SimpleNamespace(
        a=a, b=b, sA=s_a, sA2=s_a2, sB=s_b, m1=m1, m2=m2, m3a=m3a, m3b=m3b, m4=m4
    )
