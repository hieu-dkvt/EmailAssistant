"""Chạy worker xử lý nền (làm sạch + gom thread sau mỗi lần đồng bộ).

    uv run --directory backend python -m app.worker

Trên Windows dùng SelectorEventLoop (psycopg async không chạy trên Proactor). Procrastinate không
bắt tín hiệu dừng trên Windows, Ctrl+C làm asyncio hủy run_loops. Trên Linux vòng default bắt
SIGINT, SIGTERM rồi run_loops dừng vòng còn lại. Cả hai trường hợp đều ngừng nhận job mới và chờ
job đang chạy xong: job dịch có thể chờ AI tới 120 giây. Ctrl+C thêm thì hủy job, transaction đang
mở rollback, lần quét sau làm lại (mỗi bước đều idempotent).
"""

import asyncio
import logging
import sys
from typing import Any

from procrastinate import App

from app import tasks
from app.ai.provider import LlmResolver, env_llm
from app.db.session import create_api_engine
from app.ml_client import HttpMlClient
from app.processing import create_worker_engine

log = logging.getLogger("app.worker")


async def run_loops(app: App, **options: Any) -> None:
    """Hai vòng trong một process: một lần chờ AI (tới 120 giây) ở queue translate không làm nghẽn
    làm sạch, gom thread, sửa tay ở queue default (spec 26 mục 5).

    Chỉ vòng default cài signal handler, nên một vòng kết thúc (tín hiệu, lỗi) thì dừng vòng còn
    lại, nếu không process không thoát. Hủy một vòng không hủy job đang chạy: Procrastinate ngừng
    nhận job rồi chờ job đó xong. Không dùng gather cho hai vòng: khi bị hủy, gather trả về ngay
    lúc vòng đầu dừng, engine bị dispose trong khi job dịch còn chạy. Lỗi thật được ném ra.
    wait=False (chỉ dùng trong test) vì vậy dừng cả hai khi một vòng hết việc.
    """
    loops = [
        asyncio.create_task(app.run_worker_async(queues=["default"], concurrency=1, **options)),
        asyncio.create_task(
            app.run_worker_async(
                queues=[tasks.TRANSLATE_QUEUE],
                concurrency=1,
                name=tasks.TRANSLATE_QUEUE,
                install_signal_handlers=False,
                **options,
            )
        ),
    ]
    try:
        await asyncio.wait(loops, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for loop in loops:
            loop.cancel()
        results = await asyncio.gather(*loops, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            raise result


async def run() -> None:
    engine = create_worker_engine()
    # Job dịch đọc, ghi mail qua RLS: role app_api, không dùng engine worker (spec 26 mục 5).
    api_engine = create_api_engine()
    app = tasks.create_app()
    try:
        async with app.open_async():
            await tasks.defer_sweep_now(app)
            log.info("worker started")
            await run_loops(
                app,
                # ml: embedding mail (ml-service trên cùng máy; không chạy thì job embed bỏ qua).
                additional_context={
                    "engine": engine,
                    "api_engine": api_engine,
                    "ml": HttpMlClient(),
                    "resolver": LlmResolver(env_llm()),
                },
                # Hỏi bảng job định kỳ thay vì LISTEN: NOTIFY không phân quyền được, role nào
                # cũng gửi được lệnh hủy job giả. Hỏi mỗi giây: sửa tay từ web (apply_overrides
                # mỗi 5 giây) có hiệu lực nhanh.
                listen_notify=False,
                fetch_job_polling_interval=1.0,
                # Mỗi phút có một job sweep: không giữ job đã xong.
                delete_jobs="successful",
            )
    finally:
        await api_engine.dispose()
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        if sys.platform == "win32":
            asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
        else:
            asyncio.run(run())
    except KeyboardInterrupt:
        log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
