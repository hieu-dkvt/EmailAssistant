"""Làm sạch + gom thread trong DB (app/processing.py), chạy bằng role app_worker trên DB test."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import processing
from app.mail.cleaning import CLEANER_VERSION
from app.processing import needs_rethread, pending_users, process_user
from tests.dbutil import CLIENT, PM, T0, add_mail, add_user

QUOTED_REPLY = (
    "Đã nhận.\n\nOn Wed, Sep 2, 2026 at 10:00 AM Client <client@a.example.jp> wrote:\n> Báo giá\n"
)


@pytest.fixture
def two_users(owner_engine: Engine, clean: None) -> SimpleNamespace:
    """A và B cùng nhận một thread (mỗi người một bản, cùng Message-ID); A có thêm một mail lẻ."""
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        ids: dict[str, int] = {}
        for user, tag in ((a, "a"), (b, "b")):
            ids[f"root_{tag}"] = add_mail(conn, user, "root@x.example")
            ids[f"reply_{tag}"] = add_mail(
                conn,
                user,
                "reply@x.example",
                refs=("root@x.example",),
                irt="root@x.example",
                subject="Re: Báo giá",
                frm=PM,
                to=(CLIENT,),
                day=1,
                body=QUOTED_REPLY,
            )
        ids["lone_a"] = add_mail(conn, a, "lone@x.example", subject="Khác", day=3)
    return SimpleNamespace(a=a, b=b, **ids)


def fetch(conn: Connection, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in conn.execute(text(sql), params)]


async def test_process_cleans_and_threads_one_user_only(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    s = two_users
    result = await process_user(worker, s.a)
    assert (result.clean.cleaned, result.clean.failed) == (3, 0)
    assert result.threads is not None
    assert (result.threads.messages, result.threads.threads) == (3, 2)

    with owner_engine.connect() as conn:
        reply = fetch(
            conn,
            "select body_clean, cleaner_version, clean_cuts->0->>'kind' from messages "
            "where id = :id",
            id=s.reply_a,
        )
        assert reply == [("Đã nhận.", CLEANER_VERSION, "quote")]
        # B chưa được xử lý.
        assert fetch(
            conn,
            "select count(*) from messages where user_id = :b "
            "and (cleaner_version is not null or thread_id is not null)",
            b=s.b,
        ) == [(0,)]
        threads = fetch(
            conn,
            "select t.message_count, t.first_message_at, t.last_message_at "
            "from threads t join messages m on m.thread_id = t.id where m.id = :id",
            id=s.root_a,
        )
        assert threads == [(2, T0, T0 + timedelta(days=1))]
        parents = fetch(
            conn,
            "select id, thread_parent_id, thread_method from messages where user_id = :a "
            "order by id",
            a=s.a,
        )
        assert parents == [
            (s.root_a, None, "root"),
            (s.reply_a, s.root_a, "references"),
            (s.lone_a, None, "root"),
        ]

    await process_user(worker, s.b)
    with owner_engine.connect() as conn:
        # Cùng Message-ID nhưng mỗi người một thread; thread chỉ chứa mail của chủ thread.
        assert fetch(
            conn,
            "select count(*) from messages m join threads t on t.id = m.thread_id "
            "where t.user_id <> m.user_id",
        ) == [(0,)]
        a_threads = {r[0] for r in fetch(conn, "select id from threads where user_id = :u", u=s.a)}
        b_threads = {r[0] for r in fetch(conn, "select id from threads where user_id = :u", u=s.b)}
        assert len(b_threads) == 1 and not a_threads & b_threads


async def test_rerun_does_nothing(worker: AsyncEngine, two_users: SimpleNamespace) -> None:
    await process_user(worker, two_users.a)
    again = await process_user(worker, two_users.a)
    assert (again.clean.cleaned, again.threads) == (0, None)
    forced = await process_user(worker, two_users.a, force_rethread=True)
    assert forced.threads is not None and forced.threads.changed == 0


async def test_pending_users(worker: AsyncEngine, two_users: SimpleNamespace) -> None:
    s = two_users
    assert await pending_users(worker) == [s.a, s.b]
    await process_user(worker, s.a)
    assert await pending_users(worker) == [s.b]


async def test_new_message_joining_two_threads_keeps_larger_id(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    s = two_users
    with owner_engine.begin() as conn:
        # Mail trả lời một mail "missing" chưa có: thread riêng.
        orphan = add_mail(conn, s.a, "orphan@x.example", irt="missing@x.example", day=5)
    await process_user(worker, s.a)
    with owner_engine.connect() as conn:
        big = conn.scalar(text("select thread_id from messages where id = :id"), {"id": s.root_a})
        small = conn.scalar(text("select thread_id from messages where id = :id"), {"id": orphan})
    assert big != small

    with owner_engine.begin() as conn:
        # Mail "missing" tới: trả lời reply, và là cha của orphan → hai thread thành một.
        add_mail(conn, s.a, "missing@x.example", irt="reply@x.example", day=4)
    result = await process_user(worker, s.a)
    assert result.threads is not None
    assert (result.threads.created, result.threads.deleted) == (0, 1)
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(text("select thread_id from messages where id = :id"), {"id": orphan})
            == big
        )
        assert conn.scalar(text("select count(*) from threads where id = :t"), {"t": small}) == 0
        assert conn.scalar(text("select message_count from threads where id = :t"), {"t": big}) == 4


async def test_deleted_message_removes_empty_thread(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    s = two_users
    await process_user(worker, s.a)
    with owner_engine.begin() as conn:
        lone_thread = conn.scalar(
            text("select thread_id from messages where id = :id"), {"id": s.lone_a}
        )
        conn.execute(text("delete from messages where id = :id"), {"id": s.lone_a})
    assert await needs_rethread(worker, s.a)
    await process_user(worker, s.a)
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(text("select count(*) from threads where id = :t"), {"t": lone_thread}) == 0
        )
        assert (
            conn.scalar(
                text("select sum(message_count) from threads where user_id = :u"), {"u": s.a}
            )
            == 2
        )
    assert not await needs_rethread(worker, s.a)


async def test_cleaner_version_change_recleans(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    s = two_users
    await process_user(worker, s.a)
    with owner_engine.begin() as conn:
        conn.execute(
            text("update messages set cleaner_version = 0 where id = :id"), {"id": s.reply_a}
        )
    assert s.a in await pending_users(worker)
    result = await process_user(worker, s.a)
    assert result.clean.cleaned == 1


async def test_clean_failure_does_not_block_batch(
    worker: AsyncEngine,
    owner_engine: Engine,
    two_users: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = processing.clean_body

    def flaky(body: str, subject: str | None = None) -> Any:
        if body == QUOTED_REPLY:
            raise ValueError("bug")
        return real(body, subject)

    monkeypatch.setattr(processing, "clean_body", flaky)
    monkeypatch.setattr(processing, "CLEAN_BATCH", 1)  # nhiều lô: kiểm tra phân trang theo id
    s = two_users.a
    result = await process_user(worker, s)
    assert (result.clean.cleaned, result.clean.failed) == (3, 1)
    with owner_engine.connect() as conn:
        row = fetch(
            conn,
            "select body_clean = body_text, clean_cuts, cleaner_version from messages "
            "where id = :id",
            id=two_users.reply_a,
        )
        assert row == [(True, [], CLEANER_VERSION)]
        assert (
            conn.scalar(
                text(
                    "select count(*) from messages where user_id = :u "
                    "and cleaner_version is distinct from :v"
                ),
                {"u": s, "v": CLEANER_VERSION},
            )
            == 0
        )


async def test_split_override_is_applied(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    s = two_users
    await process_user(worker, s.a)
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "insert into thread_overrides (user_id, action, message_id) "
                "values (:u, 'split', :m)"
            ),
            {"u": s.a, "m": s.reply_a},
        )
    result = await process_user(worker, s.a, force_rethread=True)
    assert result.threads is not None and result.threads.threads == 3
    with owner_engine.connect() as conn:
        assert fetch(
            conn,
            "select thread_parent_id, thread_method from messages where id = :id",
            id=s.reply_a,
        ) == [(None, "root")]


async def test_messages_without_dates(
    worker: AsyncEngine, owner_engine: Engine, clean: None
) -> None:
    with owner_engine.begin() as conn:
        u = add_user(conn, "a@example.com")
        add_mail(conn, u, "x@x.example", day=None)
    result = await process_user(worker, u)
    assert result.threads is not None and result.threads.threads == 1
    with owner_engine.connect() as conn:
        assert fetch(conn, "select first_message_at, last_message_at from threads") == [
            (None, None)
        ]


async def test_user_without_messages(
    worker: AsyncEngine, owner_engine: Engine, clean: None
) -> None:
    with owner_engine.begin() as conn:
        u = add_user(conn, "a@example.com")
    result = await process_user(worker, u, force_rethread=True)
    assert result.threads is not None and result.threads.threads == 0
