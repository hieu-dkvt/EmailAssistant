"""API v1 cho collector: /me, /ingest, /sources. Mọi truy vấn chạy trong user_session (RLS)."""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import current_user_id, get_engine, read_body
from app.api.errors import validation_error_response
from app.db.session import user_session
from app.ingest import ingest_batch, parser_supported
from app.schemas import (
    DeleteFolderRequest,
    DeleteResult,
    IngestBatch,
    IngestResult,
    Me,
    SourceInfo,
)
from app.sources import delete_folder_mail, list_sources

log = logging.getLogger("app.api")
router = APIRouter(prefix="/api/v1")

UserId = Annotated[int, Depends(current_user_id)]
Engine = Annotated[AsyncEngine, Depends(get_engine)]


@router.get("/me", response_model=Me)
async def me(user_id: UserId, engine: Engine) -> Me:
    async with user_session(engine, user_id) as s:
        row = (
            await s.execute(
                text("SELECT id, email, display_name FROM users WHERE id = :id"), {"id": user_id}
            )
        ).one()
    return Me(id=row.id, email=row.email, display_name=row.display_name)


@router.post("/ingest", response_model=IngestResult)
async def ingest(request: Request, user_id: UserId, engine: Engine) -> IngestResult:
    raw = await read_body(request)
    try:
        batch = IngestBatch.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    if not parser_supported(batch.parser_version):
        raise HTTPException(
            status.HTTP_426_UPGRADE_REQUIRED,
            "Collector quá cũ: hãy cập nhật collector rồi đồng bộ lại",
        )
    started = time.monotonic()
    async with user_session(engine, user_id) as s:
        result = await ingest_batch(s, user_id, batch)
    log.info(
        "ingest user=%s source=%s received=%d inserted=%d duplicates=%d mismatched=%d ms=%d",
        user_id,
        result.source_id,
        result.received,
        result.inserted,
        result.duplicates,
        result.mismatched,
        (time.monotonic() - started) * 1000,
    )
    return result


@router.get("/sources", response_model=list[SourceInfo])
async def sources(user_id: UserId, engine: Engine) -> list[SourceInfo]:
    async with user_session(engine, user_id) as s:
        return await list_sources(s)


@router.post(
    "/sources/delete-mail",
    response_model=DeleteResult,
    responses={404: {"description": 'Không có folder: {"detail", "code": "source_not_found"}'}},
)
async def delete_mail(
    request: Request, user_id: UserId, engine: Engine
) -> DeleteResult | JSONResponse:
    # Đọc body sau khi xác thực (tham số body của FastAPI được parse trước dependency).
    raw = await read_body(request)
    try:
        req = DeleteFolderRequest.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    async with user_session(engine, user_id) as s:
        result = await delete_folder_mail(s, user_id, req.folder_path)
    if result is None:
        # Mã riêng: collector chỉ coi là "đã xóa từ trước" khi thấy mã này, không phải mọi 404.
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Không có folder này trên server", "code": "source_not_found"},
        )
    log.info(
        "delete-folder-mail user=%s deleted=%d kept=%d",
        user_id,
        result.deleted_messages,
        result.kept_messages,
    )
    return result
