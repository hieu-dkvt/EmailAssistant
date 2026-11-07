"""Kiểu chung của client AI và ghi lại mỗi lần gọi vào ai_calls (spec 21 mục 5, spec 22).

- Output có cấu trúc: JSON schema từ Pydantic (strict_schema) rồi tự kiểm tra bằng Pydantic, để
  luôn đọc được stop_reason và usage kể cả khi model từ chối / hết token.
- Client gọi gateway nằm ở provider.py (OpenAiLlm); lỗi được thay bằng LlmError (mã cho web).
- Text gửi đi là text gốc, không che (spec 28): địa chỉ ở Cài đặt AI quyết định mail đi đâu.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.models import TASKS
from app.db.session import user_session

log = logging.getLogger("app.ai")

TIMEOUT_SECONDS = 120.0
TIMEOUT_MESSAGE = "Model trả lời quá lâu, thử lại sau hoặc dùng model nhanh hơn"
MAX_MODEL_CHARS = 200


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
    ) -> LlmResponse: ...


def served_model(value: object, requested: str) -> str:
    """Tên model gateway báo đã dùng, nếu an toàn để ghi log và DB; không thì tên đã yêu cầu.

    Giá trị do gateway trả về: chuỗi có xuống dòng giả được dòng log, chuỗi dài làm đầy log.
    """
    if isinstance(value, str) and 0 < len(value) <= MAX_MODEL_CHARS and value.isprintable():
        return value
    return requested


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema của model Pydantic cho response_format strict: mọi object không nhận thêm khóa.

    Output model ở prompts.py chỉ dùng kiểu đơn giản (str, int, bool, list, None) và mọi trường
    đều bắt buộc, nên schema của Pydantic chỉ thiếu additionalProperties.
    """
    return _close_objects(model.model_json_schema())


def _close_objects(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: _close_objects(v) for k, v in node.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_close_objects(v) for v in node]
    return node


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
            schema=strict_schema(output_type),
            max_tokens=spec.max_tokens,
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
