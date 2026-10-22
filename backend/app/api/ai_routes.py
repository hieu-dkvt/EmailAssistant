"""API của web cho AI: hỏi đáp (spec 21), cài đặt model và thống kê (spec 22).

Xác thực bằng phiên web (WebUserId; request ghi cần header X-PMM-Request). Không log câu hỏi,
câu trả lời: log chỉ có id, số lượng, thời gian (trong app/ai/).
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.ask import AskBusy, AskFilters, ask
from app.ai.client import LlmClient, LlmError, configured
from app.ai.models import TASKS, TIER_INFO, TIERS, model_for
from app.ai.settings import month_start, save_tiers, usage_since, user_tiers
from app.api.deps import get_engine, read_body
from app.api.errors import ApiError, validation_error_response
from app.api.web_deps import WebUserId, json_body
from app.db.session import user_session
from app.ml_client import EmbeddingModelMismatch, MlClient, MlUnavailable
from app.web_schemas import (
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
MASKED_CATEGORIES = [
    "Email, điện thoại, URL, địa chỉ",
    "Tên người",
    "Tên công ty, tổ chức",
    "Số tiền, số hợp đồng / đơn hàng",
]


def get_ml(request: Request) -> MlClient:
    return request.app.state.ml


def get_llm(request: Request) -> LlmClient | None:
    return request.app.state.llm


async def _settings(engine: AsyncEngine, user_id: int) -> AiSettings:
    async with user_session(engine, user_id) as s:
        tiers = await user_tiers(s)
    return AiSettings(
        configured=configured(),
        tasks=[
            AiTaskInfo(
                task=t.task,
                label=t.label,
                description=t.description,
                tier=tiers[t.task],
                default_tier=t.default_tier,
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
    unknown = [t for t in req.tiers if t not in TASKS]
    if unknown:
        raise validation_error_response(
            [
                {"loc": ("body", "tiers", t), "msg": "Không có tác vụ này", "type": "unknown_task"}
                for t in unknown
            ]
        )
    async with user_session(engine, user_id) as s:
        await save_tiers(s, user_id, req.tiers)
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
    llm: Annotated[LlmClient | None, Depends(get_llm)],
) -> AskResult:
    raw = await read_body(request)
    try:
        req = AskRequest.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None
    if not req.question.strip():
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Câu hỏi đang trống", "question_empty")
    if llm is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Chưa cấu hình Claude: đặt ANTHROPIC_API_KEY cho backend rồi chạy lại",
            "ai_not_configured",
        )
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
