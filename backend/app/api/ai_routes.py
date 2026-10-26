"""API của web cho AI: hỏi đáp (spec 21), cài đặt model và thống kê (spec 22).

Xác thực bằng phiên web (WebUserId; request ghi cần header X-PMM-Request). Không log câu hỏi,
câu trả lời: log chỉ có id, số lượng, thời gian (trong app/ai/).
"""

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai import secrets
from app.ai.ask import AskBusy, AskFilters, ask
from app.ai.client import LlmError, LlmResponse, LlmUsage, configured, record_call
from app.ai.models import TASKS, TIER_INFO, TIERS, model_for
from app.ai.provider import (
    PROTOCOLS,
    LlmResolver,
    delete_provider,
    ensure_public_host,
    key_hint,
    load_provider,
    save_provider,
    validate_base_url,
)
from app.ai.settings import (
    month_start,
    save_models,
    save_tiers,
    usage_since,
    user_models,
    user_tiers,
)
from app.api.deps import get_engine, read_body
from app.api.errors import ApiError, validation_error_response
from app.api.web_deps import WebUserId, json_body
from app.db.session import user_session
from app.ml_client import EmbeddingModelMismatch, MlClient, MlUnavailable
from app.web_schemas import (
    AiProvider,
    AiProviderCustom,
    AiProviderTest,
    AiProviderUpdate,
    AiSettings,
    AiSettingsUpdate,
    AiTaskInfo,
    AiTierInfo,
    AiUsage,
    AiUsageRow,
    AskRequest,
    AskResult,
    ErrorBody,
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
MASKED_CATEGORIES = [
    "Email, điện thoại, URL, địa chỉ",
    "Tên người",
    "Tên công ty, tổ chức",
    "Số tiền, số hợp đồng / đơn hàng",
]


def get_ml(request: Request) -> MlClient:
    return request.app.state.ml


def get_resolver(request: Request) -> LlmResolver:
    return request.app.state.llm_resolver


async def _settings(engine: AsyncEngine, user_id: int) -> AiSettings:
    async with user_session(engine, user_id) as s:
        tiers = await user_tiers(s)
        models = await user_models(s)
    custom = await load_provider(engine, user_id) is not None
    return AiSettings(
        configured=custom or configured(),
        custom_provider=custom,
        tasks=[
            AiTaskInfo(
                task=t.task,
                label=t.label,
                description=t.description,
                tier=tiers[t.task],
                default_tier=t.default_tier,
                model=models.get(t.task),
            )
            for t in TASKS.values()
        ],
        tiers=[
            AiTierInfo(
                tier=tier,
                label=TIER_INFO[tier].label,
                model=model_for(tier),
                input_price=TIER_INFO[tier].input_price,
                output_price=TIER_INFO[tier].output_price,
            )
            for tier in TIERS
        ],
        masking=MASKED_CATEGORIES,
    )


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
        {"loc": ("body", field, t), "msg": "Không có tác vụ này", "type": "unknown_task"}
        for field, names in (("tiers", req.tiers), ("models", req.models))
        for t in names
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
        await save_tiers(s, user_id, req.tiers)
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
        total_estimated_usd=round(sum(r.estimated_usd or 0.0 for r in rows), 6),
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
    try:
        llm, custom = await resolver.for_user(engine, user_id)
    except LlmError as e:
        raise ApiError(e.http_status, e.message, e.code) from None
    if llm is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Chưa cấu hình AI: nhập nhà cung cấp ở Cài đặt AI hoặc đặt ANTHROPIC_API_KEY",
            "ai_not_configured",
        )
    if custom:
        async with user_session(engine, user_id) as s:
            missing = [t for t in TASKS if t not in await user_models(s)]
        if missing:
            raise ApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Nhà cung cấp tùy chỉnh cần tên model cho mọi tác vụ (Cài đặt AI)",
                "ai_model_missing",
            )
    filters = AskFilters(
        folder_id=req.folder_id, sent_after=req.sent_after, sent_before=req.sent_before
    )
    try:
        with request.app.state.ask_guard.hold(user_id):
            return await ask(
                engine, ml, llm, user_id, req.question, filters, custom_provider=custom
            )
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
        )
    return AiProvider(
        configured_env=configured(), secret_key_ready=secrets.secret_key_ready(), custom=custom
    )


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
        await ensure_public_host(base_url)
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
        llm, custom = await resolver.for_user(engine, user_id)
        if llm is None or not custom:
            raise LlmError("ai_not_configured", 503, "Chưa lưu nhà cung cấp tùy chỉnh")
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
                fallbacks=False,
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
