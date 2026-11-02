"""API của web cho AI: hỏi đáp (spec 21), cài đặt model và thống kê (spec 22).

Xác thực bằng phiên web (WebUserId; request ghi cần header X-PMM-Request). Không log câu hỏi,
câu trả lời: log chỉ có id, số lượng, thời gian (trong app/ai/).
"""

import logging
import time
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, status
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.ai import secrets
from app.ai.ask import ASK_TASKS, AskBusy, AskFilters, ask
from app.ai.client import LlmClient, LlmError, LlmResponse, LlmUsage, record_call
from app.ai.models import TASK_TRANSLATE, TASKS
from app.ai.provider import (
    PROTOCOLS,
    LlmResolver,
    delete_provider,
    is_public_host,
    key_hint,
    load_provider,
    reject_link_local,
    save_provider,
    validate_base_url,
)
from app.ai.settings import month_start, save_models, usage_since, user_models
from app.ai.translate import (
    TranslateError,
    ensure_translatable,
    produce_translation,
    read_mail,
    save_translation,
    saved_translations,
    thread_translations,
)
from app.api.deps import get_engine, read_body
from app.api.errors import ApiError, validation_error_response
from app.api.web_deps import WebUserId, json_body
from app.db.session import user_session
from app.ml_client import EmbeddingModelMismatch, MlClient, MlUnavailable
from app.schemas import MAX_BIGINT
from app.web_schemas import (
    AiProvider,
    AiProviderCustom,
    AiProviderTest,
    AiProviderUpdate,
    AiSettings,
    AiSettingsUpdate,
    AiTaskInfo,
    AiUsage,
    AiUsageRow,
    AskRequest,
    AskResult,
    ErrorBody,
    MailMoveOut,
    MessageTranslationOut,
    MoveInput,
    ThreadTranslations,
    TranslateInput,
)

log = logging.getLogger("app.api")
router = APIRouter(prefix="/api/v1")

Engine = Annotated[AsyncEngine, Depends(get_engine)]
UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody, "description": "Chưa đăng nhập"}
}
MAX_MODEL_NAME = 200
MAX_API_KEY = 500
TEST_SYSTEM = "Bạn là bộ kiểm tra kết nối. Chỉ trả về JSON theo schema."
TEST_USER = 'Trả về {"ok": true}.'
TEST_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def get_ml(request: Request) -> MlClient:
    return request.app.state.ml


def get_resolver(request: Request) -> LlmResolver:
    return request.app.state.llm_resolver


async def _settings(engine: AsyncEngine, user_id: int) -> AiSettings:
    async with user_session(engine, user_id) as s:
        models = await user_models(s)
    return AiSettings(
        configured=await load_provider(engine, user_id) is not None,
        tasks=[
            AiTaskInfo(
                task=t.task, label=t.label, description=t.description, model=models.get(t.task)
            )
            for t in TASKS.values()
        ],
    )


async def _llm_for(
    engine: AsyncEngine, resolver: LlmResolver, user_id: int, tasks: Sequence[str]
) -> LlmClient:
    """Client AI của người dùng; phải có tên model cho đúng các tác vụ cần dùng (spec 26 mục 6),
    không phải mọi tác vụ trong TASKS."""
    try:
        llm = await resolver.for_user(engine, user_id)
    except LlmError as e:
        raise ApiError(e.http_status, e.message, e.code) from None
    if llm is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Chưa cấu hình AI: nhập nhà cung cấp ở Cài đặt AI",
            "ai_not_configured",
        )
    async with user_session(engine, user_id) as s:
        models = await user_models(s)
    if any(t not in models for t in tasks):
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Cần nhập tên model cho tác vụ này ở Cài đặt AI",
            "ai_model_missing",
        )
    return llm


@router.get("/ai/settings", response_model=AiSettings, responses=UNAUTHORIZED)
async def get_settings(user_id: WebUserId, engine: Engine) -> AiSettings:
    return await _settings(engine, user_id)


@router.put(
    "/ai/settings",
    response_model=AiSettings,
    responses={**UNAUTHORIZED, 403: {"model": ErrorBody, "description": "Thiếu header"}},
    openapi_extra=json_body(AiSettingsUpdate),
)
async def put_settings(request: Request, user_id: WebUserId, engine: Engine) -> AiSettings:
    raw = await read_body(request)
    try:
        req = AiSettingsUpdate.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    errors: list[dict[str, Any]] = [
        {"loc": ("body", "models", t), "msg": "Không có tác vụ này", "type": "unknown_task"}
        for t in req.models
        if t not in TASKS
    ]
    models: dict[str, str | None] = {}
    for task, raw in req.models.items():
        name = (raw or "").strip()
        if len(name) > MAX_MODEL_NAME or any(ord(c) < 32 for c in name):
            errors.append(
                {"loc": ("body", "models", task), "msg": "Tên model không hợp lệ", "type": "model"}
            )
        models[task] = name or None
    if errors:
        raise validation_error_response(errors)
    async with user_session(engine, user_id) as s:
        await save_models(s, user_id, models)
    return await _settings(engine, user_id)


@router.get("/ai/usage", response_model=AiUsage, responses=UNAUTHORIZED)
async def get_usage(user_id: WebUserId, engine: Engine) -> AiUsage:
    since = month_start()
    async with user_session(engine, user_id) as s:
        rows = [AiUsageRow(**r) for r in await usage_since(s, since)]  # type: ignore[arg-type]
    return AiUsage(
        since=since,
        rows=rows,
        total_calls=sum(r.calls for r in rows),
    )


@router.post(
    "/ask",
    response_model=AskResult,
    responses={
        **UNAUTHORIZED,
        403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
        409: {"model": ErrorBody, "description": "ask_in_progress"},
        502: {"model": ErrorBody, "description": "ai_refused, ai_truncated, ai_bad_output…"},
        503: {"model": ErrorBody, "description": "ai_not_configured, ai_unavailable, ml_…"},
    },
    openapi_extra=json_body(AskRequest),
)
async def post_ask(
    request: Request,
    user_id: WebUserId,
    engine: Engine,
    ml: Annotated[MlClient, Depends(get_ml)],
    resolver: Annotated[LlmResolver, Depends(get_resolver)],
) -> AskResult:
    raw = await read_body(request)
    try:
        req = AskRequest.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    if not req.question.strip():
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Câu hỏi đang trống", "question_empty")
    llm = await _llm_for(engine, resolver, user_id, ASK_TASKS)
    filters = AskFilters(
        folder_id=req.folder_id, sent_after=req.sent_after, sent_before=req.sent_before
    )
    try:
        with request.app.state.ask_guard.hold(user_id):
            return await ask(engine, ml, llm, user_id, req.question, filters)
    except AskBusy:
        raise ApiError(
            status.HTTP_409_CONFLICT, "Đang có một câu hỏi chưa trả lời xong", "ask_in_progress"
        ) from None
    except LlmError as e:
        raise ApiError(e.http_status, e.message, e.code) from None
    except (ApiError, MlUnavailable, EmbeddingModelMismatch, DBAPIError):
        raise  # đã có handler riêng, không lộ nội dung
    except Exception as e:
        # Thông điệp lỗi có thể chứa câu hỏi hay nội dung mail: uvicorn sẽ log cả traceback.
        # Chỉ log tên lớp lỗi; response không có chi tiết.
        log.error("ask failed user=%s error=%s", user_id, type(e).__name__)
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Lỗi khi xử lý câu hỏi", "ask_failed"
        ) from None


# --- dịch mail (spec 26 mục 6, 10) ---


@router.post(
    "/messages/{message_id}/translate",
    response_model=MessageTranslationOut,
    responses={
        **UNAUTHORIZED,
        400: {"model": ErrorBody, "description": "nothing_to_translate"},
        403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
        404: {"model": ErrorBody, "description": "message_not_found"},
        409: {"model": ErrorBody, "description": "mail_not_ready, translate_in_progress"},
        502: {"model": ErrorBody, "description": "ai_refused, ai_truncated, ai_bad_output…"},
        503: {"model": ErrorBody, "description": "ai_not_configured, ai_unavailable…"},
    },
    openapi_extra=json_body(TranslateInput),
)
async def post_translate(
    request: Request,
    message_id: Annotated[int, Path(gt=0, le=MAX_BIGINT)],
    user_id: WebUserId,
    engine: Engine,
    resolver: Annotated[LlmResolver, Depends(get_resolver)],
) -> MessageTranslationOut:
    raw = await read_body(request)
    try:
        req = TranslateInput.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    try:
        async with user_session(engine, user_id) as s:
            mail = await read_mail(s, user_id, message_id)
            saved = {} if req.force else await saved_translations(s, user_id, [message_id])
        if message_id in saved:
            return saved[message_id]
        ensure_translatable(mail)
        llm = await _llm_for(engine, resolver, user_id, (TASK_TRANSLATE,))
        with request.app.state.translate_guard.hold(user_id):
            result = await produce_translation(engine, llm, user_id, message_id)
            async with user_session(engine, user_id) as s:
                await save_translation(s, user_id, message_id, result)
                return (await saved_translations(s, user_id, [message_id]))[message_id]
    except AskBusy:
        raise ApiError(
            status.HTTP_409_CONFLICT, "Đang có một mail chưa dịch xong", "translate_in_progress"
        ) from None
    except TranslateError as e:
        raise ApiError(e.status_code, e.detail, e.code) from None
    except LlmError as e:
        raise ApiError(e.http_status, e.message, e.code) from None
    except (ApiError, MlUnavailable, EmbeddingModelMismatch, DBAPIError):
        raise  # đã có handler riêng, không lộ nội dung
    except Exception as e:
        # Thông điệp lỗi có thể chứa nội dung mail: uvicorn sẽ log cả traceback.
        log.error("translate failed user=%s error=%s", user_id, type(e).__name__)
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Lỗi khi dịch mail", "translate_failed"
        ) from None


@router.get(
    "/threads/{thread_id}/translations",
    response_model=ThreadTranslations,
    responses={**UNAUTHORIZED, 404: {"model": ErrorBody, "description": "thread_not_found"}},
)
async def get_thread_translations(
    thread_id: Annotated[int, Path(gt=0, le=MAX_BIGINT)], user_id: WebUserId, engine: Engine
) -> ThreadTranslations:
    async with user_session(engine, user_id) as s:
        result = await thread_translations(s, user_id, thread_id)
    if result is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Không có thread này", "thread_not_found")
    return result


# --- chuyển mail sang folder dự án, chỉ trên web (spec 26 mục 8) ---

MOVE_ERRORS: dict[int | str, dict[str, Any]] = {
    **UNAUTHORIZED,
    403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
    404: {"model": ErrorBody, "description": "message_not_found"},
}
OWN_MESSAGE = text("SELECT EXISTS (SELECT 1 FROM messages WHERE id = :m AND user_id = :u)")
RUN_STATE = text(
    "SELECT status, target_source_id, move_note FROM message_auto_runs "
    "WHERE user_id = :u AND message_id = :m"
)
PROJECT_FOLDER = text(
    "SELECT p.processed_source_id FROM projects p "
    "JOIN message_projects mp ON mp.project_id = p.id AND mp.user_id = p.user_id "
    "WHERE p.id = :p AND p.user_id = :u AND mp.message_id = :m AND mp.user_id = :u "
    "AND p.processed_source_id IS NOT NULL"
)
SET_TARGET = text(
    "UPDATE message_auto_runs SET target_source_id = :t, move_note = NULL, updated_at = now() "
    "WHERE user_id = :u AND message_id = :m AND status = 'done'"
)
DELETE_FAILED_RUN = text(
    "WITH d AS (DELETE FROM message_auto_runs WHERE user_id = :u AND message_id = :m "
    "AND status = 'failed' RETURNING 1) SELECT count(*) FROM d"
)


async def _require_own_message(s: AsyncSession, user_id: int, message_id: int) -> None:
    if not await s.scalar(OWN_MESSAGE, {"m": message_id, "u": user_id}):
        raise ApiError(status.HTTP_404_NOT_FOUND, "Không có mail này", "message_not_found")


@router.post(
    "/messages/{message_id}/move-back",
    response_model=MailMoveOut,
    responses=MOVE_ERRORS,
)
async def post_move_back(
    message_id: Annotated[int, Path(gt=0, le=MAX_BIGINT)], user_id: WebUserId, engine: Engine
) -> MailMoveOut:
    """Đưa mail về chỗ cũ (Inbox). Dòng run vẫn `done`: không dịch hay chuyển lại."""
    async with user_session(engine, user_id) as s:
        await _require_own_message(s, user_id, message_id)
        await s.execute(SET_TARGET, {"t": None, "u": user_id, "m": message_id})
        row = (await s.execute(RUN_STATE, {"u": user_id, "m": message_id})).first()
    return MailMoveOut(
        message_id=message_id,
        folder_id=None if row is None else row.target_source_id,
        move_note=None if row is None else row.move_note,
    )


@router.post(
    "/messages/{message_id}/move",
    response_model=MailMoveOut,
    responses={
        **MOVE_ERRORS,
        409: {"model": ErrorBody, "description": "move_not_ready"},
        422: {"model": ErrorBody, "description": "move_invalid"},
    },
    openapi_extra=json_body(MoveInput),
)
async def post_move(
    request: Request,
    message_id: Annotated[int, Path(gt=0, le=MAX_BIGINT)],
    user_id: WebUserId,
    engine: Engine,
) -> MailMoveOut:
    """Chọn dự án cho mail (thường là `ambiguous`): dự án phải chứa mail và có folder."""
    raw = await read_body(request)
    try:
        req = MoveInput.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    async with user_session(engine, user_id) as s:
        await _require_own_message(s, user_id, message_id)
        run = (await s.execute(RUN_STATE, {"u": user_id, "m": message_id})).first()
        if run is None or run.status != "done":
            raise ApiError(
                status.HTTP_409_CONFLICT, "Mail chưa được xử lý tự động", "move_not_ready"
            )
        target = await s.scalar(
            PROJECT_FOLDER, {"p": req.project_id, "u": user_id, "m": message_id}
        )
        if target is None:
            raise ApiError(
                422,
                "Dự án không chứa mail này hoặc chưa có folder",
                "move_invalid",
            )
        await s.execute(SET_TARGET, {"t": target, "u": user_id, "m": message_id})
    return MailMoveOut(message_id=message_id, folder_id=target, move_note=None)


@router.post(
    "/messages/{message_id}/auto-retry",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **MOVE_ERRORS,
        409: {"model": ErrorBody, "description": "auto_retry_invalid"},
    },
)
async def post_auto_retry(
    message_id: Annotated[int, Path(gt=0, le=MAX_BIGINT)], user_id: WebUserId, engine: Engine
) -> None:
    """Xóa dòng `failed` để lần quét sau thử lại."""
    async with user_session(engine, user_id) as s:
        await _require_own_message(s, user_id, message_id)
        deleted = await s.scalar(DELETE_FAILED_RUN, {"u": user_id, "m": message_id})
    if not deleted:
        raise ApiError(
            status.HTTP_409_CONFLICT, "Mail không ở trạng thái lỗi", "auto_retry_invalid"
        )


# --- nhà cung cấp AI tùy chỉnh (spec 24) ---


async def _provider_info(engine: AsyncEngine, user_id: int) -> AiProvider:
    stored = await load_provider(engine, user_id)
    custom = None
    if stored is not None:
        key = stored.key()
        custom = AiProviderCustom(
            protocol=stored.protocol,
            base_url=stored.base_url,
            api_key_set=key is not None,
            api_key_hint=key_hint(key),
            public_host=await is_public_host(stored.base_url),
        )
    return AiProvider(secret_key_ready=secrets.secret_key_ready(), custom=custom)


def _field_error(field: str, msg: str, type_: str) -> Any:
    return validation_error_response([{"loc": ("body", field), "msg": msg, "type": type_}])


@router.get("/ai/provider", response_model=AiProvider, responses=UNAUTHORIZED)
async def get_provider(user_id: WebUserId, engine: Engine) -> AiProvider:
    return await _provider_info(engine, user_id)


@router.put(
    "/ai/provider",
    response_model=AiProvider,
    responses={
        **UNAUTHORIZED,
        403: {"model": ErrorBody, "description": "Thiếu header"},
        503: {"model": ErrorBody, "description": "secret_key_missing"},
    },
    openapi_extra=json_body(AiProviderUpdate),
)
async def put_provider(
    request: Request,
    user_id: WebUserId,
    engine: Engine,
    resolver: Annotated[LlmResolver, Depends(get_resolver)],
) -> AiProvider:
    raw = await read_body(request)
    try:
        req = AiProviderUpdate.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    if req.protocol not in PROTOCOLS:  # Literal đã chặn; giữ cho chắc khi thêm giao thức
        raise _field_error("protocol", "Giao thức không hợp lệ", "protocol")
    try:
        base_url = validate_base_url(req.base_url)
    except ValueError as e:
        raise _field_error("base_url", str(e), "base_url") from None
    try:
        await reject_link_local(base_url)
    except ValueError as e:
        raise _field_error("base_url", str(e), "base_url") from None
    key = (req.api_key or "").strip()
    if any(c.isspace() or ord(c) < 32 for c in key) or len(key) > MAX_API_KEY:
        raise _field_error("api_key", "API key không hợp lệ", "api_key")
    if not secrets.secret_key_ready():
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Chưa đặt PMM_SECRET_KEY cho backend nên chưa lưu được API key "
            "(python -m app.cli gen-secret-key)",
            "secret_key_missing",
        )
    if not key:
        stored = await load_provider(engine, user_id)
        key = (stored.key() if stored else None) or ""
        if not key:
            raise _field_error("api_key", "Cần nhập API key", "api_key")
    await save_provider(engine, user_id, req.protocol, base_url, key)
    resolver.forget(user_id)
    return await _provider_info(engine, user_id)


@router.delete(
    "/ai/provider",
    response_model=AiProvider,
    responses={**UNAUTHORIZED, 403: {"model": ErrorBody, "description": "Thiếu header"}},
)
async def remove_provider(
    request: Request,
    user_id: WebUserId,
    engine: Engine,
    resolver: Annotated[LlmResolver, Depends(get_resolver)],
) -> AiProvider:
    await delete_provider(engine, user_id)
    resolver.forget(user_id)
    return await _provider_info(engine, user_id)


@router.post(
    "/ai/provider/test",
    response_model=AiProviderTest,
    responses={
        **UNAUTHORIZED,
        403: {"model": ErrorBody, "description": "Thiếu header"},
        503: {"model": ErrorBody, "description": "ai_not_configured, ai_auth_failed…"},
    },
)
async def test_provider(
    user_id: WebUserId,
    engine: Engine,
    resolver: Annotated[LlmResolver, Depends(get_resolver)],
) -> AiProviderTest:
    """Gửi một câu cố định, không có dữ liệu mail, bằng model đã nhập cho tác vụ "answer"."""
    try:
        llm = await resolver.for_user(engine, user_id)
        if llm is None:
            raise LlmError("ai_not_configured", 503, "Chưa lưu nhà cung cấp AI")
        async with user_session(engine, user_id) as s:
            model = (await user_models(s)).get("answer")
        if not model:
            raise LlmError("ai_model_missing", 503, "Nhập tên model cho tác vụ trả lời trước")
        started = time.monotonic()
        status_, resp = "error", LlmResponse(None, model, None, LlmUsage())
        try:
            resp = await llm.create(
                model=model,
                system=TEST_SYSTEM,
                user=TEST_USER,
                schema=TEST_SCHEMA,
                max_tokens=50,
                effort="low",
            )
            status_ = "ok"
        finally:
            ms = int((time.monotonic() - started) * 1000)
            await record_call(
                engine, user_id, "provider_test", resp.model or model, status_, resp.usage, ms
            )
    except LlmError as e:
        raise ApiError(e.http_status, e.message, e.code) from None
    return AiProviderTest(ok=True, model=resp.model or model, duration_ms=ms)
