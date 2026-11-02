"""API của ml-service (spec 2026-10-19-ml-service-design.md mục 3).

Chỉ phục vụ backend trên cùng máy: lắng nghe 127.0.0.1, chỉ nhận Host trong ML_ALLOWED_HOSTS
(chống DNS rebinding từ trình duyệt). Không lưu gì, không log text: log chỉ có số lượng, thời gian.
"""

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.embedder import Embedder
from app.reranker import Reranker

log = logging.getLogger("ml")

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_TEXTS = 64
MAX_EMBED_CHARS = 10_000
MAX_RERANK_QUERY_CHARS = 2_000

EmbedText = Annotated[str, StringConstraints(max_length=MAX_EMBED_CHARS)]


class EmbedRequest(BaseModel):
    texts: list[EmbedText] = Field(min_length=1, max_length=MAX_TEXTS)


class EmbedResponse(BaseModel):
    model: str
    dim: int
    vectors: list[list[float]]


RerankQuery = Annotated[str, StringConstraints(min_length=1, max_length=MAX_RERANK_QUERY_CHARS)]


class RerankRequest(BaseModel):
    query: RerankQuery
    texts: list[EmbedText] = Field(min_length=1, max_length=MAX_TEXTS)


class RerankResponse(BaseModel):
    model: str
    scores: list[float]


class Health(BaseModel):
    status: str
    embed_model: str
    dim: int
    max_seq_length: int
    rerank_model: str | None = None


def create_app(
    load_embedder: Callable[[], Embedder],
    settings: Settings | None = None,
    load_reranker: Callable[[], Reranker] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    models: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        started = time.monotonic()
        models["embedder"] = await run_in_threadpool(load_embedder)
        if load_reranker is not None:
            models["reranker"] = await run_in_threadpool(load_reranker)
        log.info("models loaded ms=%d", (time.monotonic() - started) * 1000)
        yield

    app = FastAPI(
        title="PM Mail Assistant ML service",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        # FastAPI bật sẵn OpenTelemetry: tắt hẳn (service này nhận text mail).
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):  # type: ignore[no-untyped-def]
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return JSONResponse(status_code=413, content={"detail": "Body quá lớn"})
        return await call_next(request)

    def embedder() -> Embedder:
        return models["embedder"]  # type: ignore[return-value]

    def reranker() -> Reranker | None:
        return models.get("reranker")  # type: ignore[return-value]

    @app.get("/health", response_model=Health)
    def health() -> Health:
        e = embedder()
        return Health(
            status="ok",
            embed_model=e.model_name,
            dim=e.dim,
            max_seq_length=e.max_seq_length,
            rerank_model=r.model_name if (r := reranker()) else None,
        )

    def failed(what: str, exc: Exception) -> JSONResponse:
        # Bắt lỗi ngay trong endpoint: handler Exception của Starlette vẫn để uvicorn log cả
        # traceback, mà thông điệp lỗi của tokenizer có thể chứa mẩu text đầu vào.
        log.error("%s error type=%s", what, type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Lỗi ml-service"})

    # Endpoint đồng bộ (def): FastAPI chạy ở threadpool, suy luận không chặn event loop.
    @app.post("/embed", response_model=EmbedResponse)
    def embed(req: EmbedRequest) -> EmbedResponse | JSONResponse:
        started = time.monotonic()
        e = embedder()
        try:
            vectors = e.encode(req.texts)
        except Exception as exc:
            return failed("embed", exc)
        log.info("embed n=%d ms=%d", len(req.texts), (time.monotonic() - started) * 1000)
        return EmbedResponse(model=e.model_name, dim=e.dim, vectors=vectors)

    @app.post("/rerank", response_model=RerankResponse)
    def rerank(req: RerankRequest) -> RerankResponse | JSONResponse:
        started = time.monotonic()
        r = reranker()
        if r is None:
            return JSONResponse(status_code=503, content={"detail": "rerank_disabled"})
        try:
            scores = r.score(req.query, req.texts)
        except Exception as exc:
            return failed("rerank", exc)
        log.info("rerank n=%d ms=%d", len(req.texts), (time.monotonic() - started) * 1000)
        return RerankResponse(model=r.model_name, scores=scores)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> Response:
        # Mặc định FastAPI trả lại cả input (text) trong lỗi 422: chỉ trả vị trí và loại lỗi.
        errors = [{"loc": list(e.get("loc", ())), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> Response:
        # Không đưa thông điệp lỗi (có thể chứa text đầu vào) vào log hay response.
        log.error("error type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Lỗi ml-service"})

    return app
