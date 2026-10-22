"""Xác thực của web: phiên cookie và header chống CSRF (spec 2026-10-18-web-login-design.md mục 5).

Endpoint của web chỉ nhận phiên cookie; endpoint của collector chỉ nhận token Bearer (deps.py).
Token collector vì vậy không đọc được nội dung mail.
"""

from typing import Annotated, Any

from fastapi import Depends, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import get_engine
from app.api.errors import ApiError
from app.web_auth import SESSION_COOKIE, hash_session_token

CSRF_HEADER = "X-PMM-Request"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def not_authenticated() -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập", "not_authenticated")


def require_csrf_header(request: Request) -> None:
    """Request ghi phải có header tùy biến: trang web khác không gửi được nếu không qua CORS."""
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise ApiError(
            status.HTTP_403_FORBIDDEN, f"Thiếu header {CSRF_HEADER}", "csrf_header_missing"
        )


async def session_user_id(
    request: Request, engine: Annotated[AsyncEngine, Depends(get_engine)]
) -> int:
    """Cookie phiên → user_id qua hàm web_session_user (app_api không đọc được bảng phiên)."""
    require_csrf_header(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise not_authenticated()
    async with engine.connect() as conn:
        user_id = await conn.scalar(
            text("SELECT web_session_user(:h)"), {"h": hash_session_token(token)}
        )
        await conn.commit()  # lưu last_seen_at
    if user_id is None:
        raise not_authenticated()
    return user_id


WebUserId = Annotated[int, Depends(session_user_id)]


def json_body(model: type[BaseModel]) -> dict[str, Any]:
    """openapi_extra mô tả body JSON cho endpoint đọc body bằng read_body (sau xác thực), để client
    web sinh từ OpenAPI có kiểu của body."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }
