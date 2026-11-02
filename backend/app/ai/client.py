"""Gọi AI (SDK anthropic) và ghi lại mỗi lần gọi vào ai_calls (spec 21 mục 5, spec 22).

- Output có cấu trúc: structured outputs (output_config.format, schema từ Pydantic) rồi tự kiểm tra
  bằng Pydantic, để luôn đọc được stop_reason và usage kể cả khi model từ chối / hết token.
- Lỗi SDK được thay bằng LlmError (mã cho web); log chỉ có tên lớp lỗi, không có thông điệp.
- Text gửi đi là text gốc, không che (spec 28): địa chỉ ở Cài đặt AI quyết định mail đi đâu.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.models import TASKS, Effort
from app.db.session import user_session

log = logging.getLogger("app.ai")

TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 2


@dataclass(frozen=True)
class LlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class LlmResponse:
    text: str | None  # text của khối text đầu tiên (JSON theo schema)
    model: str  # model thực sự trả lời (có thể là model dự phòng)
    stop_reason: str | None
    usage: LlmUsage


class LlmError(Exception):
    """Lỗi gọi AI cho web: code (vd. ai_unavailable), http_status, status ghi ai_calls."""

    def __init__(
        self,
        code: str,
        http_status: int,
        message: str,
        *,
        status: str = "error",
        usage: LlmUsage | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message
        self.status = status
        self.usage = usage or LlmUsage()
        self.model = model


class LlmClient(Protocol):
    async def create(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, object],
        max_tokens: int,
        effort: Effort,
    ) -> LlmResponse: ...


def _quiet_sdk_logs() -> None:
    # ANTHROPIC_LOG=debug làm SDK ghi cả request (chứa text mail gốc) vào log: giữ mức WARNING.
    for name in ("anthropic", "httpx2", "httpcore2", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class AnthropicLlm:
    def __init__(self, client: AsyncAnthropic) -> None:
        # Client luôn do _anthropic_llm (provider.py) dựng với base_url, key của người dùng:
        # không có đường nào đọc khóa hay địa chỉ từ biến môi trường.
        self._client = client

    async def create(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, object],
        max_tokens: int,
        effort: Effort,
    ) -> LlmResponse:
        output_config = {"effort": effort, "format": {"type": "json_schema", "schema": schema}}
        messages = [{"role": "user", "content": user}]
        try:
            resp = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,  # type: ignore[arg-type]
                output_config=output_config,  # type: ignore[arg-type]
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise _api_error("ai_auth_failed", 503, "Khóa API của AI không hợp lệ", e) from None
        except anthropic.RateLimitError as e:
            raise _api_error("ai_unavailable", 503, "AI đang quá tải, thử lại sau", e) from None
        except anthropic.BadRequestError as e:
            raise _api_error("ai_bad_request", 502, "AI từ chối request", e) from None
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise _api_error("ai_unavailable", 503, "AI đang lỗi, thử lại sau", e) from None
            raise _api_error("ai_bad_request", 502, "AI từ chối request", e) from None
        except anthropic.APIConnectionError as e:  # gồm APITimeoutError
            raise _api_error("ai_unavailable", 503, "Không kết nối được AI", e) from None
        usage = resp.usage
        text_block = next((b.text for b in resp.content if b.type == "text"), None)
        return LlmResponse(
            text=text_block,
            model=resp.model,
            stop_reason=resp.stop_reason,
            usage=LlmUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )


def _api_error(code: str, http_status: int, message: str, e: Exception) -> LlmError:
    log.warning(
        "ai api error type=%s request_id=%s", type(e).__name__, getattr(e, "request_id", None)
    )
    return LlmError(code, http_status, message)


@dataclass
class CallRecord:
    task: str
    model: str
    status: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


@dataclass
class CallLog:
    """Các lần gọi AI trong một lần hỏi (trả về cho web, không có nội dung)."""

    calls: list[CallRecord] = field(default_factory=list)


INSERT_CALL = text(
    """
    INSERT INTO ai_calls (user_id, task, model, status, input_tokens, output_tokens,
                          cache_read_tokens, cache_write_tokens, duration_ms)
    VALUES (:u, :task, :model, :status, :in_, :out, :cr, :cw, :ms)
    """
)


async def record_call(
    engine: AsyncEngine, user_id: int, task: str, model: str, status: str, usage: LlmUsage, ms: int
) -> None:
    async with user_session(engine, user_id) as s:
        await s.execute(
            INSERT_CALL,
            {
                "u": user_id,
                "task": task,
                "model": model,
                "status": status,
                "in_": usage.input_tokens,
                "out": usage.output_tokens,
                "cr": usage.cache_read_tokens,
                "cw": usage.cache_write_tokens,
                "ms": ms,
            },
        )
    log.info(
        "ai user=%s task=%s model=%s status=%s in=%d out=%d ms=%d",
        user_id,
        task,
        model,
        status,
        usage.input_tokens,
        usage.output_tokens,
        ms,
    )


async def call_task[T: BaseModel](
    engine: AsyncEngine,
    llm: LlmClient,
    user_id: int,
    task: str,
    model: str,
    *,
    system: str,
    user: str,
    output_type: type[T],
    log_to: CallLog,
) -> T:
    """Gọi AI cho một tác vụ bằng tên model người dùng nhập; ghi ai_calls mọi trường hợp."""
    spec = TASKS[task]
    started = time.monotonic()
    status = "error"
    usage = LlmUsage()
    served = model
    try:
        resp = await llm.create(
            model=model,
            system=system,
            user=user,
            schema=transform_schema(output_type),
            max_tokens=spec.max_tokens,
            effort=spec.effort,
        )
        usage, served = resp.usage, resp.model or model
        if resp.stop_reason == "refusal":
            status = "refusal"
            raise LlmError("ai_refused", 502, "AI từ chối trả lời câu hỏi này", status=status)
        if resp.stop_reason == "max_tokens":
            status = "max_tokens"
            raise LlmError("ai_truncated", 502, "Câu trả lời của AI bị cắt giữa chừng")
        try:
            parsed = output_type.model_validate_json(resp.text or "")
        except ValidationError:
            raise LlmError("ai_bad_output", 502, "AI trả kết quả sai định dạng") from None
        status = "ok"
        return parsed
    finally:
        ms = int((time.monotonic() - started) * 1000)
        try:
            await record_call(engine, user_id, task, served, status, usage, ms)
        except Exception as e:  # không che lỗi gốc của lần gọi
            log.error("ai_calls insert failed type=%s", type(e).__name__)
        log_to.calls.append(
            CallRecord(task, served, status, usage.input_tokens, usage.output_tokens, ms)
        )
