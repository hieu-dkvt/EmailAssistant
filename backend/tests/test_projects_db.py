"""Bảng dự án và liên kết mail (spec 25 mục 4): RLS, GRANT, FK kép, xóa lan truyền."""

import asyncio
import logging
import os
import subprocess
import sys
from types import SimpleNamespace

import psycopg.errors
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.processing import process_user
from app.projects import (
    AssignStats,
    assign_batch,
    assign_messages,
    assign_user,
    load_rules,
    pending_rule_users,
    reset_scan,
)
from tests.dbutil import BACKEND_DIR, add_mail, add_project, add_user, as_user, url_for_test_db


def link(conn, user_id: int, message_id: int, project_id: int) -> None:  # noqa: ANN001
    conn.execute(
        text("insert into message_projects (user_id, message_id, project_id) values (:u, :m, :p)"),
        {"u": user_id, "m": message_id, "p": project_id},
    )


def count(conn, table: str) -> int:  # noqa: ANN001
    return conn.scalar(text(f"select count(*) from {table}"))


def test_project_is_private(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "Dự án A")
    with api_engine.begin() as conn:
        as_user(conn, seeded.a)
        assert list(conn.scalars(text("select id from projects"))) == [pid]
    with api_engine.begin() as conn:
        as_user(conn, seeded.b)
        assert list(conn.scalars(text("select id from projects"))) == []
    with api_engine.begin() as conn:
        assert list(conn.scalars(text("select id from projects"))) == []


def test_api_role_cannot_write_message_projects(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        link(conn, seeded.a, seeded.m1, pid)
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)


def test_api_role_cannot_set_project_id_or_change_owner(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(
            text(
                "insert into projects (id, user_id, name) "
                "overriding system value values (999, :u, 'x')"
            ),
            {"u": seeded.a},
        )
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(
            text("update projects set user_id = :b where id = :id"), {"b": seeded.b, "id": pid}
        )
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(text("update projects set id = default where id = :id"), {"id": pid})
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    # Sửa tên (cột được phép) thì chạy.
    with api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(text("update projects set name = 'Q' where id = :id"), {"id": pid})


def test_message_projects_rejects_other_users_project(
    owner_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid_b = add_project(conn, seeded.b, "PB")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        link(conn, seeded.a, seeded.m1, pid_b)
    # Mail của người khác cũng bị chặn.
    with owner_engine.begin() as conn:
        pid_a = add_project(conn, seeded.a, "PA")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        link(conn, seeded.a, seeded.m2, pid_a)


def test_deleting_message_removes_link(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
        link(conn, seeded.a, seeded.m1, pid)
        conn.execute(text("delete from messages where id = :id"), {"id": seeded.m1})
        assert count(conn, "message_projects") == 0
        assert count(conn, "projects") == 1


def test_deleting_project_removes_link_keeps_message(
    owner_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
        link(conn, seeded.a, seeded.m1, pid)
        conn.execute(text("delete from projects where id = :id"), {"id": pid})
        assert count(conn, "message_projects") == 0
        assert (
            conn.scalar(text("select count(*) from messages where id = :id"), {"id": seeded.m1})
            == 1
        )


def test_project_name_unique_per_user(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    with owner_engine.begin() as conn:
        add_project(conn, seeded.a, "Chung")
        add_project(conn, seeded.b, "Chung")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_project(conn, seeded.a, "Chung")


# --- worker gán mail vào dự án (spec 25 mục 5) ---


@pytest.fixture
def two_users(owner_engine: Engine, clean: None) -> SimpleNamespace:
    with owner_engine.begin() as conn:
        return SimpleNamespace(a=add_user(conn, "a@example.com"), b=add_user(conn, "b@example.com"))


def set_rules_version(engine: Engine, user_id: int, version: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into user_project_state (user_id, rules_version) values (:u, :v) "
                "on conflict (user_id) do update set rules_version = :v"
            ),
            {"u": user_id, "v": version},
        )


def state(engine: Engine, user_id: int) -> tuple[int, int, int, int]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select rules_version, applied_rules_version, scan_version, scan_after_id "
                "from user_project_state where user_id = :u"
            ),
            {"u": user_id},
        ).one()
    return row[0], row[1], row[2], row[3]


def linked(engine: Engine, user_id: int) -> set[tuple[str, int]]:
    """Cặp (Message-ID giả, project_id) đã gán."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select m.message_id, mp.project_id from message_projects mp "
                "join messages m on m.id = mp.message_id where mp.user_id = :u"
            ),
            {"u": user_id},
        )
        return {(r[0], r[1]) for r in rows}


async def test_new_mail_assigned_by_process_user(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_subject=["báo giá"])
        add_mail(conn, two_users.a, "m1@x.example", subject="Re: Báo giá tháng 9")
        add_mail(conn, two_users.a, "m2@x.example", subject="Họp")
    await process_user(worker, two_users.a)
    assert linked(owner_engine, two_users.a) == {("m1@x.example", pid)}
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from messages where thread_id is null")) == 0


async def test_no_projects_is_noop(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        add_mail(conn, two_users.a, "m1@x.example")
    await process_user(worker, two_users.a)
    assert linked(owner_engine, two_users.a) == set()


async def test_process_user_second_run_no_duplicates(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        add_mail(conn, two_users.a, "m1@x.example")
    await process_user(worker, two_users.a)
    await process_user(worker, two_users.a, force_rethread=True)
    assert linked(owner_engine, two_users.a) == {("m1@x.example", pid)}


async def test_scan_adds_and_removes_on_filter_change(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_subject=["alpha"])
        add_mail(conn, two_users.a, "a@x.example", subject="alpha")
        add_mail(conn, two_users.a, "b@x.example", subject="beta")
    await process_user(worker, two_users.a)
    assert linked(owner_engine, two_users.a) == {("a@x.example", pid)}
    with owner_engine.begin() as conn:
        conn.execute(
            text("update projects set filter_subject = '{beta}' where id = :id"), {"id": pid}
        )
    set_rules_version(owner_engine, two_users.a, 1)
    stats = await assign_user(worker, two_users.a)
    assert stats == AssignStats(messages=2, more=False)
    assert linked(owner_engine, two_users.a) == {("b@x.example", pid)}
    assert state(owner_engine, two_users.a)[:2] == (1, 1)
    # Đã áp dụng xong thì không làm gì nữa.
    assert await assign_user(worker, two_users.a) == AssignStats(0, False)


async def test_scan_runs_in_batches(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        for i in range(5):
            add_mail(conn, two_users.a, f"m{i}@x.example")
    set_rules_version(owner_engine, two_users.a, 1)
    assert await assign_batch(worker, two_users.a, 2) == AssignStats(2, more=True)
    assert state(owner_engine, two_users.a)[1] == 0  # chưa xong thì chưa đánh dấu đã áp dụng
    assert await assign_batch(worker, two_users.a, 2) == AssignStats(2, more=True)
    assert await assign_batch(worker, two_users.a, 2) == AssignStats(1, more=False)
    assert len(linked(owner_engine, two_users.a)) == 5
    assert state(owner_engine, two_users.a)[1] == 1


async def test_assign_user_stops_at_budget_with_more(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        for i in range(5):
            add_mail(conn, two_users.a, f"m{i}@x.example")
    set_rules_version(owner_engine, two_users.a, 1)
    stats = await assign_user(worker, two_users.a, budget_seconds=0, batch=2)
    assert stats == AssignStats(2, more=True)
    stats = await assign_user(worker, two_users.a, batch=2)
    assert stats == AssignStats(3, more=False)


async def test_filter_change_mid_scan_restarts(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_subject=["alpha"])
        for i in range(3):
            add_mail(conn, two_users.a, f"a{i}@x.example", subject="alpha")
        for i in range(2):
            add_mail(conn, two_users.a, f"b{i}@x.example", subject="beta")
    set_rules_version(owner_engine, two_users.a, 1)
    assert (await assign_batch(worker, two_users.a, 2)).more is True
    assert state(owner_engine, two_users.a)[3] > 0
    with owner_engine.begin() as conn:
        conn.execute(
            text("update projects set filter_subject = '{beta}' where id = :id"), {"id": pid}
        )
    set_rules_version(owner_engine, two_users.a, 2)
    stats = await assign_user(worker, two_users.a, batch=2)
    assert stats.messages == 5 and stats.more is False
    assert linked(owner_engine, two_users.a) == {("b0@x.example", pid), ("b1@x.example", pid)}
    assert state(owner_engine, two_users.a)[:3] == (2, 2, 2)


async def test_scan_covers_mail_with_smaller_id_via_thread_path(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_subject=["alpha"])
        add_mail(conn, two_users.a, "old@x.example", subject="alpha")
    set_rules_version(owner_engine, two_users.a, 1)
    await assign_user(worker, two_users.a)
    with owner_engine.begin() as conn:
        late = add_mail(conn, two_users.a, "late@x.example", subject="alpha")
        # Mốc quét đã vượt id của mail mới (mail commit muộn có id nhỏ hơn mốc).
        conn.execute(
            text("update user_project_state set scan_after_id = :a where user_id = :u"),
            {"a": late + 1000, "u": two_users.a},
        )
    await process_user(worker, two_users.a)
    assert ("late@x.example", pid) in linked(owner_engine, two_users.a)


async def test_assign_skips_project_deleted_after_rules_loaded(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        mid = add_mail(conn, two_users.a, "m1@x.example")
    async with AsyncSession(worker) as s, s.begin():
        rules = await load_rules(s, two_users.a)
        assert [r.id for r in rules] == [pid]
        with owner_engine.begin() as conn:
            conn.execute(text("delete from projects where id = :id"), {"id": pid})
        await assign_messages(s, two_users.a, [mid], rules)
    assert linked(owner_engine, two_users.a) == set()


async def test_load_rules_drops_empty_values(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        add_project(conn, two_users.a, "P", filter_from=["", "A.Example.JP"])
    async with AsyncSession(worker) as s:
        (rule,) = await load_rules(s, two_users.a)
    assert rule.from_ == ("a.example.jp",)


async def test_user_isolation(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        add_mail(conn, two_users.a, "a@x.example")
        add_mail(conn, two_users.b, "b@x.example")
    await process_user(worker, two_users.a)
    await process_user(worker, two_users.b)
    set_rules_version(owner_engine, two_users.a, 1)
    await assign_user(worker, two_users.a)
    assert linked(owner_engine, two_users.a) == {("a@x.example", pid)}
    assert linked(owner_engine, two_users.b) == set()


async def test_multiple_projects(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        p1 = add_project(conn, two_users.a, "P1", filter_from=["a.example.jp"])
        p2 = add_project(conn, two_users.a, "P2", filter_subject=["báo giá"])
        add_mail(conn, two_users.a, "m1@x.example")
    await process_user(worker, two_users.a)
    assert linked(owner_engine, two_users.a) == {("m1@x.example", p1), ("m1@x.example", p2)}


async def test_delete_folder_mail_removes_links(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        mid = add_mail(conn, two_users.a, "m1@x.example")
    await process_user(worker, two_users.a)
    assert len(linked(owner_engine, two_users.a)) == 1
    with owner_engine.begin() as conn:
        conn.execute(text("delete from messages where id = :id"), {"id": mid})
    assert linked(owner_engine, two_users.a) == set()


async def test_reset_scan_requires_state(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    assert await reset_scan(worker, two_users.a) is False
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        add_mail(conn, two_users.a, "m1@x.example")
    set_rules_version(owner_engine, two_users.a, 1)
    await assign_user(worker, two_users.a)
    with owner_engine.begin() as conn:
        conn.execute(text("delete from message_projects"))
    assert await pending_rule_users(worker) == []
    assert await reset_scan(worker, two_users.a) is True
    assert await pending_rule_users(worker) == [two_users.a]
    await assign_user(worker, two_users.a)
    assert linked(owner_engine, two_users.a) == {("m1@x.example", pid)}


async def test_worker_logs_have_no_filter_values(
    worker: AsyncEngine,
    owner_engine: Engine,
    two_users: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with owner_engine.begin() as conn:
        add_project(conn, two_users.a, "Tên-dự-án-bí-mật", filter_subject=["từ-khóa-bí-mật"])
        add_mail(conn, two_users.a, "m1@x.example", subject="từ-khóa-bí-mật")
    await process_user(worker, two_users.a)
    set_rules_version(owner_engine, two_users.a, 1)
    await assign_user(worker, two_users.a)
    assert "assign user=" in caplog.text
    assert "bí-mật" not in caplog.text and "Tên-dự-án" not in caplog.text


async def hold_delete_then_assign(
    owner_engine: Engine,
    worker: AsyncEngine,
    user_id: int,
    delete_sql: str,
    params: dict,
    run,  # noqa: ANN001
) -> None:
    """Giữ một DELETE chưa commit, chạy `run(session)` ở task khác, commit DELETE sau ~0,5 giây."""
    conn = owner_engine.connect()
    trans = conn.begin()
    try:
        conn.execute(text(delete_sql), params)

        async def work() -> None:
            async with AsyncSession(worker) as s, s.begin():
                await run(s)

        task = asyncio.create_task(work())
        await asyncio.sleep(0.5)
        assert not task.done()  # đang chờ khóa FK của DELETE chưa commit
        trans.commit()
        await asyncio.wait_for(task, 10)
    finally:
        if trans.is_active:
            trans.rollback()
        conn.close()


async def test_assign_survives_project_delete_in_flight(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        gone = add_project(conn, two_users.a, "Gone", filter_from=["a.example.jp"])
        kept = add_project(conn, two_users.a, "Kept", filter_from=["a.example.jp"])
        mid = add_mail(conn, two_users.a, "m1@x.example")

    async def run(s: AsyncSession) -> None:
        rules = await load_rules(s, two_users.a)
        assert await assign_messages(s, two_users.a, [mid], rules) == 1

    await hold_delete_then_assign(
        owner_engine, worker, two_users.a, "delete from projects where id = :id", {"id": gone}, run
    )
    assert linked(owner_engine, two_users.a) == {("m1@x.example", kept)}


async def test_assign_survives_mail_delete_in_flight(
    worker: AsyncEngine, owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        gone = add_mail(conn, two_users.a, "gone@x.example")
        add_mail(conn, two_users.a, "kept@x.example")
        ids = [gone, gone + 1]

    async def run(s: AsyncSession) -> None:
        rules = await load_rules(s, two_users.a)
        await assign_messages(s, two_users.a, ids, rules)

    await hold_delete_then_assign(
        owner_engine, worker, two_users.a, "delete from messages where id = :id", {"id": gone}, run
    )
    assert linked(owner_engine, two_users.a) == {("kept@x.example", pid)}


def test_cli_assign_projects_real_entry_point(
    owner_engine: Engine, two_users: SimpleNamespace
) -> None:
    """Chạy `python -m app.cli` thật: lệnh phải được định nghĩa trước khối __main__."""
    with owner_engine.begin() as conn:
        add_project(conn, two_users.a, "P", filter_from=["a.example.jp"])
        add_mail(conn, two_users.a, "m1@x.example")
    set_rules_version(owner_engine, two_users.a, 1)
    env = {
        **os.environ,
        "DATABASE_URL_WORKER": url_for_test_db("worker"),
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "assign-projects", "--email", "a@example.com"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert f"Người dùng id={two_users.a}: đã xét 1 mail, 1 mail có dự án." in result.stdout


# --- RLS theo hành vi (rà soát bảo mật 2026-10-31) ---


def test_other_user_sees_no_links_or_state(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
        link(conn, seeded.a, seeded.m1, pid)
        conn.execute(
            text("insert into user_project_state (user_id, rules_version) values (:u, 1)"),
            {"u": seeded.a},
        )
    with api_engine.begin() as conn:
        as_user(conn, seeded.a)
        assert count(conn, "message_projects") == 1
        assert count(conn, "user_project_state") == 1
    with api_engine.begin() as conn:
        as_user(conn, seeded.b)
        assert count(conn, "message_projects") == 0
        assert count(conn, "user_project_state") == 0


def test_other_user_cannot_update_state(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text("insert into user_project_state (user_id, rules_version) values (:u, 1)"),
            {"u": seeded.a},
        )
    with api_engine.begin() as conn:
        as_user(conn, seeded.b)
        res = conn.execute(
            text("update user_project_state set rules_version = 99 where user_id = :u"),
            {"u": seeded.a},
        )
        assert res.rowcount == 0
    assert state(owner_engine, seeded.a)[0] == 1


@pytest.mark.parametrize("column", ["applied_rules_version", "scan_version", "scan_after_id"])
def test_api_cannot_update_worker_progress_columns(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace, column: str
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text("insert into user_project_state (user_id, rules_version) values (:u, 1)"),
            {"u": seeded.a},
        )
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(text(f"update user_project_state set {column} = 5"))
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)


def test_worker_cannot_write_projects(
    owner_engine: Engine, worker_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
    for sql in (
        "update projects set name = 'x'",
        "delete from projects",
        "insert into projects (user_id, name) values (:u, 'y')",
    ):
        with pytest.raises(ProgrammingError) as ei, worker_engine.begin() as conn:
            as_user(conn, seeded.a)
            conn.execute(text(sql), {"u": seeded.a} if ":u" in sql else {})
        assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege), sql
    with owner_engine.begin() as conn:
        assert conn.scalar(text("select id from projects")) == pid


def test_api_cannot_insert_project_for_other_user(
    api_engine: Engine, owner_engine: Engine, seeded: SimpleNamespace
) -> None:
    """RLS WITH CHECK: phiên của A không chèn được dự án mang user_id của B."""
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(
            text("insert into projects (user_id, name) values (:u, 'Giả mạo')"), {"u": seeded.b}
        )
    assert getattr(ei.value.orig, "sqlstate", None) == "42501"
    with owner_engine.connect() as conn:
        assert count(conn, "projects") == 0
