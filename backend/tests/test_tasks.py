"""Worker Procrastinate chạy thật trên DB test (spec 2026-10-17-worker-design.md mục 7)."""

import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from procrastinate import App
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import embedding, processing, tasks
from app.processing import pending_users
from tests.dbutil import add_mail, add_user, url_for_test_db
from tests.mlfake import FakeMl

JOB_TABLES = (
    "procrastinate.procrastinate_events",
    "procrastinate.procrastinate_periodic_defers",
    "procrastinate.procrastinate_jobs",
    "procrastinate.procrastinate_workers",
)


@pytest.fixture
async def job_app(owner_engine: Engine, migrated: None) -> AsyncIterator[App]:
    with owner_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(JOB_TABLES)} RESTART IDENTITY CASCADE"))
    app = tasks.create_app(url_for_test_db("worker"))
    async with app.open_async():
        yield app


@pytest.fixture
def users(owner_engine: Engine, clean: None) -> SimpleNamespace:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        for u in (a, b):
            add_mail(conn, u, "root@x.example")
            add_mail(conn, u, "reply@x.example", irt="root@x.example", subject="Re: Báo giá", day=1)
    return SimpleNamespace(a=a, b=b)


def job_statuses(engine: Engine) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select task_name, status from procrastinate.procrastinate_jobs order by id")
        )
        return [(r.task_name, r.status) for r in rows]


async def run_until_idle(app: App, engine: AsyncEngine) -> None:
    await app.run_worker_async(
        wait=False, concurrency=1, additional_context={"engine": engine}, listen_notify=False
    )


def test_job_schema_privileges(owner_engine: Engine, migrated: None) -> None:
    with owner_engine.connect() as conn:
        usage = {
            role: conn.scalar(
                text("select has_schema_privilege(:r, 'procrastinate', 'USAGE')"), {"r": role}
            )
            for role in ("app_api", "app_worker")
        }
        in_public = conn.scalar(
            text(
                "select count(*) from pg_tables where tablename like 'procrastinate%' "
                "and schemaname = 'public'"
            )
        )
    assert usage == {"app_api": False, "app_worker": True}
    assert in_public == 0


async def test_defer_pending_once_per_user(
    job_app: App, worker: AsyncEngine, owner_engine: Engine, users: SimpleNamespace
) -> None:
    assert await tasks.defer_pending(job_app, worker) == 2
    assert await tasks.defer_pending(job_app, worker) == 0  # đã có job chờ cho mỗi người
    assert job_statuses(owner_engine) == [
        (tasks.PROCESS_USER_MAIL, "todo"),
        (tasks.PROCESS_USER_MAIL, "todo"),
    ]

    await run_until_idle(job_app, worker)
    assert await pending_users(worker) == []
    assert {s for _, s in job_statuses(owner_engine)} == {"succeeded"}
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(
                text("select count(*) from messages where thread_id is null or body_clean is null")
            )
            == 0
        )


async def test_job_error_log_has_no_mail_content(
    job_app: App,
    worker: AsyncEngine,
    owner_engine: Engine,
    clean: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with owner_engine.begin() as conn:
        u = add_user(conn, "a@example.com")
        add_mail(conn, u, "s@x.example", subject="SECRET-TASK-SUBJECT", body="SECRET-TASK-BODY")
    real = processing.build_threads

    def bad(rows: Any, overrides: Any = ()) -> Any:  # UPDATE lỗi CHECK, DETAIL chứa cả dòng
        result = real(rows, overrides)
        methods: dict[int, Any] = result.method
        for k in methods:
            methods[k] = "guess"
        return result

    monkeypatch.setattr(processing, "build_threads", bad)
    await tasks.defer_pending(job_app, worker)
    with caplog.at_level(logging.INFO):
        await run_until_idle(job_app, worker)
    assert "constraint=ck_messages_thread_method" in caplog.text
    assert "SECRET" not in caplog.text
    # Sweep định kỳ có thể đẩy lại job cho người dùng vẫn còn việc tồn: mọi lần đều lỗi.
    statuses = [s for name, s in job_statuses(owner_engine) if name == tasks.PROCESS_USER_MAIL]
    assert statuses and set(statuses) == {"failed"}


def add_split(engine: Engine, user_id: int, message_id: int) -> None:
    """Như API tạo sửa tay: ghi override và tăng overrides_version trong một transaction."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into thread_overrides (user_id, action, message_id) "
                "values (:u, 'split', :m)"
            ),
            {"u": user_id, "m": message_id},
        )
        conn.execute(
            text(
                "insert into user_thread_state (user_id, overrides_version) values (:u, 1) "
                "on conflict (user_id) do update "
                "set overrides_version = user_thread_state.overrides_version + 1"
            ),
            {"u": user_id},
        )


def versions(engine: Engine, user_id: int) -> tuple[int, int]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select overrides_version, applied_overrides_version from user_thread_state "
                "where user_id = :u"
            ),
            {"u": user_id},
        ).one()
    return row[0], row[1]


async def test_apply_overrides_task(
    job_app: App, worker: AsyncEngine, owner_engine: Engine, users: SimpleNamespace
) -> None:
    await tasks.defer_pending(job_app, worker)
    await run_until_idle(job_app, worker)
    with owner_engine.connect() as conn:
        reply = conn.scalar(
            text("select id from messages where user_id = :u and message_id = 'reply@x.example'"),
            {"u": users.a},
        )
    add_split(owner_engine, users.a, reply)
    assert await processing.pending_override_users(worker) == [users.a]
    assert await pending_users(worker) == [users.a]
    # Task 5 giây chỉ đẩy job cho người có sửa tay chưa áp dụng.
    await job_app.configure_task(tasks.APPLY_OVERRIDES).defer_async(timestamp=0)
    await run_until_idle(job_app, worker)
    assert versions(owner_engine, users.a) == (1, 1)
    assert await processing.pending_override_users(worker) == []
    with owner_engine.connect() as conn:
        n_threads = conn.scalar(
            text("select count(distinct thread_id) from messages where user_id = :u"),
            {"u": users.a},
        )
    assert n_threads == 2


async def test_override_committed_during_rethread_is_not_lost(
    worker: AsyncEngine,
    owner_engine: Engine,
    users: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await processing.process_user(worker, users.a)
    with owner_engine.connect() as conn:
        reply = conn.scalar(
            text("select id from messages where user_id = :u and message_id = 'reply@x.example'"),
            {"u": users.a},
        )
    add_split(owner_engine, users.a, reply)
    real = processing.build_threads

    def racing(rows: Any, overrides: Any = ()) -> Any:
        # Sửa tay thứ hai commit khi rethread đã đọc version và override.
        add_split(owner_engine, users.a, reply)
        return real(rows, overrides)

    monkeypatch.setattr(processing, "build_threads", racing)
    await processing.rethread_user(worker, users.a)
    monkeypatch.setattr(processing, "build_threads", real)
    # Chỉ version đã đọc trước khi đọc override được đánh dấu là đã áp dụng.
    assert versions(owner_engine, users.a) == (2, 1)
    assert await processing.needs_rethread(worker, users.a)
    await processing.process_user(worker, users.a)
    assert versions(owner_engine, users.a) == (2, 2)


def chunk_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.scalar(text("select count(*) from message_chunks")) or 0


async def test_embed_after_processing_and_sweep(
    job_app: App, worker: AsyncEngine, owner_engine: Engine, users: SimpleNamespace
) -> None:
    ml = FakeMl()
    context = {"engine": worker, "ml": ml}
    await tasks.defer_pending(job_app, worker)
    # process_user_mail làm sạch xong thì tự đẩy embed_user cho người đó.
    await job_app.run_worker_async(
        wait=False, concurrency=1, additional_context=context, listen_notify=False
    )
    assert chunk_count(owner_engine) == 4
    assert await embedding.pending_users(worker) == []
    names = [n for n, _ in job_statuses(owner_engine)]
    assert names.count(tasks.EMBED_USER) == 2

    # Mail mới đã làm sạch nhưng ml-service tắt: job không lỗi, không ghi gì; bật lại thì sweep
    # đẩy job và embed nốt.
    with owner_engine.begin() as conn:
        add_mail(conn, users.a, "late@x.example", subject="Báo giá bổ sung", day=3)
    await processing.process_user(worker, users.a)
    ml.down = True
    await job_app.configure_task(tasks.EMBED_SWEEP).defer_async(timestamp=1)
    await job_app.run_worker_async(
        wait=False, concurrency=1, additional_context=context, listen_notify=False
    )
    assert chunk_count(owner_engine) == 4
    assert "failed" not in {s for _, s in job_statuses(owner_engine)}
    ml.down = False
    await job_app.configure_task(tasks.EMBED_SWEEP).defer_async(timestamp=2)
    await job_app.run_worker_async(
        wait=False, concurrency=1, additional_context=context, listen_notify=False
    )
    assert chunk_count(owner_engine) == 5


async def test_embed_tasks_skip_without_ml(
    job_app: App, worker: AsyncEngine, owner_engine: Engine, users: SimpleNamespace
) -> None:
    await processing.process_user(worker, users.a)
    await job_app.configure_task(tasks.EMBED_SWEEP).defer_async(timestamp=1)
    await run_until_idle(job_app, worker)  # context không có "ml"
    assert chunk_count(owner_engine) == 0


async def test_sweep_task_processes_everything(
    job_app: App, worker: AsyncEngine, users: SimpleNamespace
) -> None:
    await tasks.defer_sweep_now(job_app)
    await tasks.defer_sweep_now(job_app)  # đã có sweep chờ: bỏ qua, không lỗi
    await run_until_idle(job_app, worker)
    assert await pending_users(worker) == []
