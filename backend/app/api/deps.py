"""Dependency dùng chung: engine, xác thực token collector, đọc body có giới hạn."""

import zlib
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import hash_token

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Token không hợp lệ",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_engine(request: Request) -> AsyncEngine:
    return request.app.state.engine


async def current_user_id(
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    authorization: Annotated[str | None, Header()] = None,
) -> int:
    """Token → user_id qua hàm auth_token_user (app_api không đọc được bảng token)."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise UNAUTHORIZED
    async with engine.connect() as conn:
        user_id = await conn.scalar(text("SELECT auth_token_user(:h)"), {"h": hash_token(token)})
        await conn.commit()  # lưu last_used_at
    if user_id is None:
        raise UNAUTHORIZED
    return user_id


async def read_body(request: Request) -> bytes:
    """Đọc body theo giới hạn của app; giải nén gzip có giới hạn (chống gzip bomb)."""
    limits = request.app.state.limits
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limits.max_body_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Dữ liệu gửi lên quá lớn")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if request.headers.get("content-encoding", "").strip().lower() != "gzip":
        return raw
    decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decomp.decompress(raw, limits.max_decompressed_bytes + 1)
    except zlib.error as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dữ liệu gzip không hợp lệ") from e
    if len(out) > limits.max_decompressed_bytes or decomp.unconsumed_tail:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Dữ liệu gửi lên quá lớn")
    if not decomp.eof:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dữ liệu gzip không hợp lệ")
    return out
