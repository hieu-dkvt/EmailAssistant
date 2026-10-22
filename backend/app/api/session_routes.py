"""Đăng nhập web: /api/v1/session (spec 2026-10-18-web-login-design.md mục 5)."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_engine, read_body
from app.api.errors import ApiError, validation_error_response
from app.api.web_deps import WebUserId, json_body, require_csrf_header
from app.db.session import user_session
from app.schemas import Me
from app.web_auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    LoginThrottle,
    dummy_hash,
    hash_session_token,
    new_session_token,
    verify_password,
)
from app.web_schemas import ErrorBody, LoginRequest

log = logging.getLogger("app.api")
router = APIRouter(prefix="/api/v1")

Engine = Annotated[AsyncEngine, Depends(get_engine)]
COOKIE_PATH = "/api"


async def _me(engine: AsyncEngine, user_id: int) -> Me:
    async with user_session(engine, user_id) as s:
        row = (
            await s.execute(
                text("SELECT id, email, display_name FROM users WHERE id = :id"), {"id": user_id}
            )
        ).one()
    return Me(id=row.id, email=row.email, display_name=row.display_name)


@router.post(
    "/session",
    response_model=Me,
    responses={
        401: {"model": ErrorBody, "description": "Sai email hoặc mật khẩu"},
        403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
        429: {"model": ErrorBody, "description": "Đăng nhập sai quá nhiều lần"},
    },
    openapi_extra=json_body(LoginRequest),
)
async def login(request: Request, response: Response, engine: Engine) -> Me:
    require_csrf_header(request)
    raw = await read_body(request)
    try:
        req = LoginRequest.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    email = req.email.strip().lower()
    throttle: LoginThrottle = request.app.state.login_throttle
    # Giữ chỗ trước khi băm: lần thử đang chạy cũng được tính vào giới hạn.
    if not throttle.begin(email):
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Đăng nhập sai quá nhiều lần, thử lại sau 15 phút",
            "too_many_attempts",
        )
    ok = False
    row = None
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT user_id, password_hash FROM web_password_hash(:e)"), {"e": email}
                )
            ).first()
        # Không có người dùng / chưa có mật khẩu: vẫn chạy scrypt để thời gian trả lời như nhau.
        stored = row.password_hash if row is not None else dummy_hash()
        # Mỗi lần scrypt dùng khoảng 32 MiB RAM: giới hạn số lần chạy cùng lúc (endpoint không cần
        # xác thực, không để nhiều request đồng thời làm cạn bộ nhớ).
        async with request.app.state.password_slots:
            ok = await run_in_threadpool(verify_password, req.password, stored) and row is not None
    finally:
        throttle.finish(email, ok)
    if not ok or row is None:
        log.info("login failed")
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "Email hoặc mật khẩu không đúng", "invalid_credentials"
        )
    token = new_session_token()
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT web_session_start(:u, :h, :exp)"),
            {
                "u": row.user_id,
                "h": hash_session_token(token),
                "exp": datetime.now(UTC) + SESSION_TTL,
            },
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    log.info("login user=%s", row.user_id)
    return await _me(engine, row.user_id)


@router.get(
    "/session",
    response_model=Me,
    responses={401: {"model": ErrorBody, "description": "Chưa đăng nhập"}},
)
async def current_session(user_id: WebUserId, engine: Engine) -> Me:
    return await _me(engine, user_id)


@router.delete(
    "/session",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"}},
)
async def logout(request: Request, engine: Engine) -> Response:
    require_csrf_header(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT web_session_end(:h)"), {"h": hash_session_token(token)})
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response
