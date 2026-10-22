"""Chạy API bằng uvicorn trên 127.0.0.1.

    uv run --directory backend python -m app.run [--port 8000]

Trên Windows dùng SelectorEventLoop: psycopg async không chạy được trên ProactorEventLoop
(mặc định của Windows).
"""

import argparse
import asyncio
import logging
import sys

import uvicorn

ACCESS_LOGGER = "uvicorn.access"


class DropQueryString(logging.Filter):
    """Access log của uvicorn ghi cả query string: ?q= (từ khóa tìm kiếm), ?sender= (địa chỉ) có
    thể chứa tên khách, nội dung nghiệp vụ. Chỉ giữ path (spec 14 mục 5: không log chuỗi tìm kiếm).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def make_server(host: str, port: int) -> uvicorn.Server:
    config = uvicorn.Config("app.main:app", host=host, port=port)
    # Config đã cấu hình logging của uvicorn; gắn filter sau đó.
    access = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, DropQueryString) for f in access.filters):
        access.addFilter(DropQueryString())
    return uvicorn.Server(config)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.run")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    server = make_server(args.host, args.port)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        server.run()


if __name__ == "__main__":
    main()
