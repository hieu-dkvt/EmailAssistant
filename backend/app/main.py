"""Ứng dụng FastAPI. Chạy dev: uv run --directory backend python -m app.run"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.ai.ask import AskGuard
from app.ai.client import LlmClient, default_llm
from app.ai.provider import LlmResolver
from app.api import (
    ai_routes,
    glossary_routes,
    project_routes,
    routes,
    session_routes,
    web_routes,
)
from app.api.errors import (
    ApiError,
    api_error_handler,
    db_error_handler,
    ml_error_handler,
    request_validation_handler,
)
from app.db.session import create_api_engine
from app.ml_client import EmbeddingModelMismatch, HttpMlClient, MlClient, MlUnavailable
from app.web_auth import LoginThrottle
from app.web_static import SecurityHeaders, SPAStaticFiles, web_dist_dir

MIB = 1024 * 1024
PASSWORD_CHECKS_AT_ONCE = 4  # scrypt chạy cùng lúc khi đăng nhập (mỗi lần ~32 MiB)
DEFAULT_ALLOWED_HOSTS = "127.0.0.1,localhost"


def allowed_hosts() -> list[str]:
    """Header Host được nhận (PMM_ALLOWED_HOSTS, phân cách bằng dấu phẩy; mặc định máy cục bộ)."""
    raw = os.environ.get("PMM_ALLOWED_HOSTS") or DEFAULT_ALLOWED_HOSTS
    return [h.strip() for h in raw.split(",") if h.strip()]


@dataclass(frozen=True)
class Limits:
    max_body_bytes: int = 16 * MIB
    max_decompressed_bytes: int = 64 * MIB


def create_app(
    engine: AsyncEngine | None = None,
    *,
    max_body_bytes: int = Limits.max_body_bytes,
    max_decompressed_bytes: int = Limits.max_decompressed_bytes,
    web_dist: Path | Literal["auto"] | None = "auto",
    ml: MlClient | None = None,
    llm: LlmClient | None = None,
) -> FastAPI:
    """web_dist: thư mục build của web; "auto" = PMM_WEB_DIST hoặc web/dist nếu đã build.

    ml: client ml-service (mặc định HTTP tới ML_SERVICE_URL). llm: client AI (mặc định
    AnthropicLlm nếu đã cấu hình khóa API, không thì None → hỏi đáp trả 503 ai_not_configured).
    """
    owns_engine = engine is None
    api_engine = engine or create_api_engine()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_engine:
            await api_engine.dispose()

    app = FastAPI(
        title="PM Mail Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        # FastAPI bật sẵn OpenTelemetry (trace/metric/log của request). Tắt hẳn: không có
        # gì về request được đưa ra ngoài nếu môi trường có cấu hình exporter.
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
        # /docs, /redoc tải script từ CDN: script ngoài chạy trên cùng origin với phiên web thì
        # gọi được API đọc mail. Tắt; OpenAPI vẫn ở /openapi.json (và python -m app.openapi_dump).
        docs_url=None,
        redoc_url=None,
    )
    app.state.engine = api_engine
    app.state.ml = ml or HttpMlClient()
    app.state.llm = llm if llm is not None else default_llm()
    app.state.llm_resolver = LlmResolver(app.state.llm)
    app.state.ask_guard = AskGuard()
    app.state.translate_guard = AskGuard()
    app.state.limits = Limits(max_body_bytes, max_decompressed_bytes)
    app.state.login_throttle = LoginThrottle()
    app.state.password_slots = asyncio.Semaphore(PASSWORD_CHECKS_AT_ONCE)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(DBAPIError, db_error_handler)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(MlUnavailable, ml_error_handler)
    app.add_exception_handler(EmbeddingModelMismatch, ml_error_handler)
    app.add_middleware(SecurityHeaders)
    # Thêm sau cùng = chạy đầu tiên. Chống DNS rebinding: trang độc trên tên miền trỏ về
    # 127.0.0.1 được trình duyệt coi là cùng origin, gửi được header X-PMM-Request.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
    app.include_router(routes.router)
    app.include_router(session_routes.router)
    app.include_router(web_routes.router)
    app.include_router(ai_routes.router)
    app.include_router(project_routes.router)
    app.include_router(glossary_routes.router)
    dist = web_dist_dir() if web_dist == "auto" else web_dist
    if dist is not None:
        # Mount sau cùng: chỉ nhận đường dẫn không khớp route API nào.
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
