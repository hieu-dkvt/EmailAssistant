"""API của web: folder, thread, mail, search, sửa tay, trạng thái xử lý.

Spec: 2026-10-18-keyword-search-design.md, 2026-10-18-thread-api-design.md. Mọi endpoint xác thực
bằng phiên web (WebUserId, kèm kiểm tra header chống CSRF cho request ghi) và chạy trong
user_session (RLS). Log chỉ có id, số lượng, thời gian.
"""

import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import get_engine, read_body
from app.api.errors import ApiError, validation_error_response
from app.api.web_deps import WebUserId, json_body
from app.db.session import user_session
from app.ml_client import MlClient
from app.overrides import OverrideError, create_override, delete_override, list_overrides
from app.schemas import MAX_BIGINT
from app.search.keyword import MessageFilters, SortOrder, search_messages
from app.search.query import MAX_QUERY_CHARS, ParsedQuery, QueryError, parse_query
from app.search.semantic import MAX_RESULTS, hybrid_search
from app.search.similar import similar_threads
from app.sources import list_folders
from app.threads_view import (
    BadCursor,
    get_thread,
    list_threads,
    message_full,
    message_ref,
    processing_status,
)
from app.web_schemas import (
    ErrorBody,
    FolderInfo,
    MessageFull,
    MessageRef,
    OverrideCreate,
    OverrideInfo,
    ProcessingStatus,
    SearchResult,
    SimilarThreads,
    ThreadDetail,
    ThreadPage,
)

log = logging.getLogger("app.api")
router = APIRouter(prefix="/api/v1")

Engine = Annotated[AsyncEngine, Depends(get_engine)]
# Giới hạn theo bigint: số lớn hơn không được làm lỗi 500 ở DB.
ThreadId = Annotated[int, Path(gt=0, le=MAX_BIGINT)]
MessageId = Annotated[int, Path(gt=0, le=MAX_BIGINT)]
OverrideId = Annotated[int, Path(gt=0, le=MAX_BIGINT)]
UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody, "description": "Chưa đăng nhập"}
}
BAD_QUERY: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorBody, "description": "Chuỗi tìm kiếm không hợp lệ"}
}


def _parse(q: str | None) -> ParsedQuery | None:
    try:
        return parse_query(q)
    except QueryError as e:
        raise ApiError(status.HTTP_400_BAD_REQUEST, e.message, e.code) from None


def message_filters(
    folder_id: Annotated[int | None, Query(gt=0, le=MAX_BIGINT)] = None,
    sender: Annotated[str | None, Query(max_length=200)] = None,
    sent_after: datetime | None = None,
    sent_before: datetime | None = None,
    has_attachments: bool | None = None,
) -> MessageFilters:
    return MessageFilters(
        folder_id=folder_id,
        sender=(sender or "").replace("\x00", "").strip() or None,
        sent_after=sent_after,
        sent_before=sent_before,
        has_attachments=has_attachments,
    )


Filters = Annotated[MessageFilters, Depends(message_filters)]


def get_ml(request: Request) -> MlClient:
    return request.app.state.ml


@router.get("/folders", response_model=list[FolderInfo], responses=UNAUTHORIZED)
async def folders(user_id: WebUserId, engine: Engine) -> list[FolderInfo]:
    async with user_session(engine, user_id) as s:
        return [FolderInfo(**f.model_dump()) for f in await list_folders(s)]


@router.get(
    "/search",
    response_model=SearchResult,
    responses={
        **UNAUTHORIZED,
        **BAD_QUERY,
        503: {"model": ErrorBody, "description": "ml_unavailable, embedding_model_mismatch"},
    },
)
async def search(
    user_id: WebUserId,
    engine: Engine,
    filters: Filters,
    ml: Annotated[MlClient, Depends(get_ml)],
    q: Annotated[str | None, Query(max_length=2000)] = None,
    sort: SortOrder = "date",
    mode: Literal["keyword", "hybrid"] = "keyword",
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SearchResult:
    if mode == "hybrid":
        text_q = (q or "").replace("\x00", "").strip()
        if not text_q:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST, "Cần nhập nội dung cần tìm", "query_required"
            )
        # Nhánh từ khóa dùng cú pháp như ô tìm kiếm (AND, OR, -từ, "cụm"): câu tự nhiên thường
        # không khớp gì nên không thêm nhiễu, mã/cụm chính xác thì được đẩy lên (spec 19 mục 10:
        # nhánh "một trong các từ" làm kết quả kém hẳn).
        try:
            keyword_query = parse_query(text_q)
        except QueryError:
            keyword_query = None  # câu quá dài / chỉ có từ phủ định: chỉ tìm theo nghĩa
        # Embed trước khi mở phiên DB: không giữ transaction trong lúc chờ ml-service.
        (vector,) = await ml.embed([text_q[:MAX_QUERY_CHARS]])
        async with user_session(engine, user_id) as s:
            return await hybrid_search(
                s,
                vector,
                user_id,
                keyword_query,
                filters,
                offset=min(offset, MAX_RESULTS),
                limit=limit,
            )
    parsed = _parse(q)
    async with user_session(engine, user_id) as s:
        return await search_messages(
            s, user_id, parsed, filters, sort=sort, offset=offset, limit=limit
        )


@router.get(
    "/threads",
    response_model=ThreadPage,
    responses={**UNAUTHORIZED, 400: {"model": ErrorBody, "description": "Cursor hỏng"}},
)
async def threads(
    user_id: WebUserId,
    engine: Engine,
    filters: Filters,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ThreadPage:
    async with user_session(engine, user_id) as s:
        try:
            return await list_threads(s, filters, cursor, limit)
        except BadCursor:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST, "Cursor không hợp lệ", "bad_cursor"
            ) from None


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadDetail,
    responses={**UNAUTHORIZED, **BAD_QUERY, 404: {"model": ErrorBody, "description": "Không có"}},
)
async def thread(
    thread_id: ThreadId,
    user_id: WebUserId,
    engine: Engine,
    q: Annotated[str | None, Query(max_length=2000)] = None,
) -> ThreadDetail:
    parsed = _parse(q)
    async with user_session(engine, user_id) as s:
        detail = await get_thread(s, thread_id, list(parsed.keywords) if parsed else [])
    if detail is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Không có thread này", "thread_not_found")
    return detail


@router.get(
    "/threads/{thread_id}/similar",
    response_model=SimilarThreads,
    responses={**UNAUTHORIZED, 404: {"model": ErrorBody, "description": "Không có"}},
)
async def thread_similar(thread_id: ThreadId, user_id: WebUserId, engine: Engine) -> SimilarThreads:
    async with user_session(engine, user_id) as s:
        found = await similar_threads(s, thread_id)
    if found is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Không có thread này", "thread_not_found")
    return found


@router.get(
    "/messages/{message_id}",
    response_model=MessageRef,
    responses={**UNAUTHORIZED, 404: {"model": ErrorBody, "description": "Không có"}},
)
async def message(message_id: MessageId, user_id: WebUserId, engine: Engine) -> MessageRef:
    async with user_session(engine, user_id) as s:
        ref = await message_ref(s, message_id)
    if ref is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Không có mail này", "message_not_found")
    return ref


@router.get(
    "/messages/{message_id}/full",
    response_model=MessageFull,
    responses={**UNAUTHORIZED, **BAD_QUERY, 404: {"model": ErrorBody, "description": "Không có"}},
)
async def message_full_text(
    message_id: MessageId,
    user_id: WebUserId,
    engine: Engine,
    q: Annotated[str | None, Query(max_length=2000)] = None,
) -> MessageFull:
    parsed = _parse(q)
    async with user_session(engine, user_id) as s:
        full = await message_full(s, message_id, list(parsed.keywords) if parsed else [])
    if full is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Không có mail này", "message_not_found")
    return full


@router.get("/processing", response_model=ProcessingStatus, responses=UNAUTHORIZED)
async def processing(user_id: WebUserId, engine: Engine) -> ProcessingStatus:
    async with user_session(engine, user_id) as s:
        return await processing_status(s)


@router.get("/thread-overrides", response_model=list[OverrideInfo], responses=UNAUTHORIZED)
async def overrides(user_id: WebUserId, engine: Engine) -> list[OverrideInfo]:
    async with user_session(engine, user_id) as s:
        return await list_overrides(s, user_id)


@router.post(
    "/thread-overrides",
    response_model=OverrideInfo,
    status_code=status.HTTP_201_CREATED,
    responses={
        **UNAUTHORIZED,
        200: {"model": OverrideInfo, "description": "Đã có sửa tay giống hệt"},
        400: {"model": ErrorBody, "description": "cannot_split_root, same_thread"},
        403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
        404: {"model": ErrorBody, "description": "message_not_found"},
        409: {"model": ErrorBody, "description": "not_threaded"},
    },
    openapi_extra=json_body(OverrideCreate),
)
async def add_override(
    request: Request, user_id: WebUserId, engine: Engine
) -> OverrideInfo | JSONResponse:
    # Đọc body sau khi xác thực (tham số body của FastAPI được parse trước dependency).
    raw = await read_body(request)
    try:
        req = OverrideCreate.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    try:
        async with user_session(engine, user_id) as s:
            info, created = await create_override(s, user_id, req)
    except OverrideError as e:
        raise ApiError(e.status_code, e.detail, e.code) from None
    log.info(
        "override user=%s action=%s override=%s created=%s", user_id, req.action, info.id, created
    )
    if not created:
        return JSONResponse(status_code=status.HTTP_200_OK, content=info.model_dump(mode="json"))
    return info


@router.delete(
    "/thread-overrides/{override_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **UNAUTHORIZED,
        403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
        404: {"model": ErrorBody, "description": "override_not_found"},
    },
)
async def remove_override(override_id: OverrideId, user_id: WebUserId, engine: Engine) -> Response:
    async with user_session(engine, user_id) as s:
        deleted = await delete_override(s, user_id, override_id)
    if not deleted:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Không có sửa tay này", "override_not_found")
    log.info("override-delete user=%s override=%s", user_id, override_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
