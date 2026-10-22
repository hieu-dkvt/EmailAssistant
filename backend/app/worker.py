"""Chạy worker xử lý nền (làm sạch + gom thread sau mỗi lần đồng bộ).

    uv run --directory backend python -m app.worker

Trên Windows dùng SelectorEventLoop (psycopg async không chạy trên Proactor). Procrastinate không
bắt tín hiệu dừng trên Windows: Ctrl+C hủy job đang chạy, transaction đang mở rollback, lần quét
sau làm lại (mỗi bước đều idempotent).
"""

import asyncio
import logging
import sys

from app import tasks
from app.ml_client import HttpMlClient
from app.processing import create_worker_engine

log = logging.getLogger("app.worker")


async def run() -> None:
    engine = create_worker_engine()
    app = tasks.create_app()
    try:
        async with app.open_async():
            await tasks.defer_sweep_now(app)
            log.info("worker started")
            await app.run_worker_async(
                concurrency=1,
                # ml: embedding mail (ml-service trên cùng máy; không chạy thì job embed bỏ qua).
                additional_context={"engine": engine, "ml": HttpMlClient()},
                # Hỏi bảng job định kỳ thay vì LISTEN: NOTIFY không phân quyền được, role nào
                # cũng gửi được lệnh hủy job giả. Hỏi mỗi giây: sửa tay từ web (apply_overrides
                # mỗi 5 giây) có hiệu lực nhanh.
                listen_notify=False,
                fetch_job_polling_interval=1.0,
                # Mỗi phút có một job sweep: không giữ job đã xong.
                delete_jobs="successful",
            )
    finally:
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
