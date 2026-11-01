# ruff: noqa: F811
"""Task dịch tự động trên queue riêng, chạy bằng worker Procrastinate thật trên DB test (spec 26
mục 5). Dữ liệu giả."""

import asyncio
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from procrastinate import App
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import auto_translate, processing, tasks
from app import worker as worker_main
from app.ai.client import LlmError
from app.ai.provider import LlmResolver
from app.auto_translate import RunStats
from tests.dbutil import add_mail
from tests.mlfake import FakeLlm, FakeMl
from tests.test_ai_api import api, fresh_people_cache, llm, ml  # noqa: F401
from tests.test_auto_translate import OUT, put, run_count, world  # noqa: F401
from tests.test_tasks import job_app  # noqa: F401


def jobs(engine: Engine, task: str) -> list[tuple[str, str]]:
    """(queue, status) của các job của task, theo thứ tự tạo."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select queue_name, status from procrastinate.procrastinate_jobs "
                "where task_name = :t order by id"
            ),
            {"t": task},
        )
        return [(r.queue_name, r.status) for r in rows]


def context(worker: AsyncEngine, api: AsyncEngine, ml: FakeMl, llm: FakeLlm) -> dict[str, Any]:
    return {"engine": worker, "api_engine": api, "ml": ml, "resolver": LlmResolver(llm)}


async def run_queue(app: App, queue: str, ctx: dict[str, Any]) -> None:
    await app.run_worker_async(
        queues=[queue], wait=False, concurrency=1, additional_context=ctx, listen_notify=False
    )


async def wait_for_job(engine: Engine, task: str, status: str) -> None:
    deadline = time.monotonic() + 30
    while (task_jobs := jobs(engine, task)) and task_jobs[-1][1] != status:
        assert time.monotonic() < deadline, task_jobs
        await asyncio.sleep(0.05)


async def test_sweep_defers_user_on_translate_queue(
    job_app: App,
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
) -> None:
    await put(world, "m1@x.example")
    ctx = context(world.worker, api, ml, llm)
    await job_app.configure_task(tasks.TRANSLATE_SWEEP).defer_async(timestamp=0)
    await run_queue(job_app, "default", ctx)
    # Worker có thể tự đẩy thêm một sweep định kỳ khi khởi động.
    assert set(jobs(world.owner, tasks.TRANSLATE_SWEEP)) == {("default", "succeeded")}
    assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "todo")]
    # Đã có job chờ của người đó: không đẩy thêm.
    assert await tasks.defer_translate(job_app, world.a) == 0


async def test_sweep_skips_without_ml(
    job_app: App, api: AsyncEngine, world: SimpleNamespace
) -> None:
    await put(world, "m1@x.example")
    ctx = {"engine": world.worker, "api_engine": api, "resolver": LlmResolver(None)}
    await job_app.configure_task(tasks.TRANSLATE_SWEEP).defer_async(timestamp=0)
    await run_queue(job_app, "default", ctx)
    assert jobs(world.owner, tasks.TRANSLATE_USER) == []


async def test_no_self_defer_after_transient_error(
    job_app: App,
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
) -> None:
    """Review Focus 3: gateway sập thì không tự đẩy thành vòng lặp, không tăng attempts."""
    await put(world, "m1@x.example")
    await put(world, "m2@x.example", day=2)
    llm.responses = [LlmError("ai_unavailable", 503, "")]
    assert await tasks.defer_translate(job_app, world.a) == 1
    await run_queue(job_app, "translate", context(world.worker, api, ml, llm))
    assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "succeeded")]
    assert len(llm.requests) == 1
    assert run_count(world.owner) == 0


async def test_self_defer_when_more(
    job_app: App,
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
) -> None:
    await put(world, "m1@x.example")
    await put(world, "m2@x.example", day=2)
    llm.responses = [OUT, OUT]
    await tasks.defer_translate(job_app, world.a)
    await run_queue(job_app, "translate", context(world.worker, api, ml, llm))
    assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "succeeded")] * 2
    assert run_count(world.owner) == 2


async def test_unexpected_error_in_task_is_sanitized(
    job_app: App,
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("SECRET-MAIL-TEXT")

    monkeypatch.setattr(auto_translate, "translate_next", boom)
    await tasks.defer_translate(job_app, world.a)
    await run_queue(job_app, "translate", context(world.worker, api, ml, llm))
    assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "failed")]
    assert "translate_user" in caplog.text
    assert "SECRET" not in caplog.text


async def test_two_worker_loops_process_their_own_queue(
    job_app: App,
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Job dịch đang chờ AI không chặn queue default. Chạy đúng hàm mà python -m app.worker gọi."""
    started, release = asyncio.Event(), asyncio.Event()
    process_user = processing.process_user

    async def blocked(*args: Any, **kwargs: Any) -> RunStats:
        started.set()
        await release.wait()
        return RunStats(False, False)

    async def after_translate_started(*args: Any, **kwargs: Any) -> Any:
        await started.wait()  # vòng default chỉ xong sau khi job dịch đã chặn
        return await process_user(*args, **kwargs)

    monkeypatch.setattr(auto_translate, "translate_next", blocked)
    monkeypatch.setattr(processing, "process_user", after_translate_started)
    with world.owner.begin() as conn:
        add_mail(conn, world.a, "plain@x.example")
    await tasks.defer_translate(job_app, world.a)
    await tasks.defer_pending(job_app, world.worker)

    loops = asyncio.create_task(
        worker_main.run_loops(
            job_app,
            additional_context=context(world.worker, api, ml, llm),
            wait=False,
            listen_notify=False,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 30)
        await wait_for_job(world.owner, tasks.PROCESS_USER_MAIL, "succeeded")
        assert jobs(world.owner, tasks.PROCESS_USER_MAIL) == [("default", "succeeded")]
        assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "doing")]
    finally:
        release.set()
        await asyncio.wait_for(loops, 30)
    assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "succeeded")]


async def wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("stopped", ["default", tasks.TRANSLATE_QUEUE])
async def test_stopping_one_loop_ends_run_loops(
    job_app: App,
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    stopped: str,
) -> None:
    """Trên Linux tín hiệu dừng chỉ gọi Worker.stop() của vòng default (vòng kia không cài handler).
    Gọi đúng hàm đó trên worker thật: run_loops phải trả về, không treo vì vòng còn lại."""
    workers: list[Any] = []
    make_worker = job_app._worker  # pyright: ignore[reportPrivateUsage]

    def capture(**kwargs: Any) -> Any:
        created = make_worker(**kwargs)
        workers.append(created)
        return created

    monkeypatch.setattr(job_app, "_worker", capture)
    loops = asyncio.create_task(
        worker_main.run_loops(
            job_app,
            additional_context=context(world.worker, api, ml, llm),
            listen_notify=False,
            fetch_job_polling_interval=0.05,
        )
    )
    try:
        await wait_until(lambda: len(workers) == 2 and all(w.worker_id for w in workers))
        next(w for w in workers if w.queues == [stopped]).stop()
        await asyncio.wait_for(loops, 30)
    finally:
        loops.cancel()
    assert all(w.worker_id is None for w in workers)  # cả hai đã dừng hẳn


class LoopStub:
    """App giả: test quyết định vòng nào kết thúc, có lỗi hay không."""

    def __init__(self) -> None:
        self.running: set[str] = set()
        self.cancelled: set[str] = set()
        self.finish = asyncio.Event()
        self.failing: str | None = None

    async def run_worker_async(self, *, queues: list[str], **options: Any) -> None:
        queue = queues[0]
        self.running.add(queue)
        if queue != self.failing:
            try:
                await self.finish.wait()
            except asyncio.CancelledError:
                self.cancelled.add(queue)
                raise
        else:
            await asyncio.sleep(0)
            raise RuntimeError("loop down")


@pytest.mark.parametrize(
    ("failing", "other"),
    [("default", tasks.TRANSLATE_QUEUE), (tasks.TRANSLATE_QUEUE, "default")],
)
async def test_loop_failure_propagates_and_stops_other(failing: str, other: str) -> None:
    stub: Any = LoopStub()
    stub.failing = failing
    with pytest.raises(RuntimeError, match="loop down"):
        await asyncio.wait_for(worker_main.run_loops(stub), 5)
    assert stub.cancelled == {other}


async def test_cancelling_run_loops_cancels_both() -> None:
    stub: Any = LoopStub()
    loops = asyncio.create_task(worker_main.run_loops(stub))
    await wait_until(lambda: len(stub.running) == 2)
    loops.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(loops, 5)
    assert stub.cancelled == {"default", tasks.TRANSLATE_QUEUE}


async def test_ctrl_c_waits_for_running_job(
    job_app: App,
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows: Ctrl+C lần đầu hủy main task, tương đương hủy run_loops. Job đang chờ AI được chạy
    nốt, không bị hủy giữa chừng."""
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args: Any, **kwargs: Any) -> RunStats:
        started.set()
        await release.wait()
        return RunStats(False, False)

    monkeypatch.setattr(auto_translate, "translate_next", blocked)
    await tasks.defer_translate(job_app, world.a)
    loops = asyncio.create_task(
        worker_main.run_loops(
            job_app,
            additional_context=context(world.worker, api, ml, llm),
            listen_notify=False,
            fetch_job_polling_interval=0.05,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 30)
        loops.cancel()
        await asyncio.sleep(0.2)
        assert not loops.done()
        assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "doing")]
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(loops, 30)
    assert jobs(world.owner, tasks.TRANSLATE_USER) == [("translate", "succeeded")]
