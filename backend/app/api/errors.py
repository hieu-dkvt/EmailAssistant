"""Lỗi trả về và ghi log không được chứa giá trị gửi lên (có thể là nội dung mail)."""

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.ml_client import EmbeddingModelMismatch

log = logging.getLogger("app.api")


class ApiError(Exception):
    """Lỗi có mã riêng cho client: body {"detail", "code"} (client phân biệt theo code)."""

    def __init__(
        self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.headers = headers


async def api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
        headers=exc.headers,
    )


def sanitize(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": e.get("type")}
        for e in errors
    ]


def validation_error_response(errors: Iterable[Mapping[str, Any]]) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=sanitize(errors))


async def request_validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": sanitize(exc.errors())},
    )


async def ml_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """ml-service không chạy / chạy model khác cấu hình (spec 19 mục 3, spec 21 mục 4)."""
    if isinstance(exc, EmbeddingModelMismatch):
        code = "embedding_model_mismatch"
        detail = (
            f"ml-service đang chạy model {exc.got}, khác PMM_EMBED_MODEL={exc.want}. "
            "Đổi model phải embed lại toàn bộ mail."
        )
    else:
        code = "ml_unavailable"
        detail = "Không gọi được ml-service (python -m app.run trong ml-service)"
    log.warning("ml error path=%s code=%s", request.url.path, code)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": detail, "code": code}
    )


async def db_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Thông điệp lỗi của PostgreSQL có thể chứa dòng dữ liệu (DETAIL: Failing row contains ...).

    Chỉ ghi mã lỗi và tên ràng buộc, không ghi thông điệp hay traceback.
    """
    assert isinstance(exc, DBAPIError)
    orig = exc.orig
    diag = getattr(orig, "diag", None)
    log.error(
        "db error path=%s sqlstate=%s constraint=%s type=%s",
        request.url.path,
        getattr(orig, "sqlstate", None),
        getattr(diag, "constraint_name", None),
        type(orig).__name__,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Lỗi cơ sở dữ liệu"},
    )
