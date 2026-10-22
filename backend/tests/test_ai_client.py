"""AnthropicLlm với transport HTTP giả: request SDK gửi đi đúng dạng, đọc đúng response và lỗi.

Không gọi Anthropic thật (không cần khóa API).
"""

import json
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from app.ai.client import AnthropicLlm, LlmError, anthropic_base_url

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def make(handler: Any) -> tuple[AnthropicLlm, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client = AsyncAnthropic(
        api_key="test-key",
        base_url="http://anthropic.test",
        max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(record)),
    )
    return AnthropicLlm(client), seen


def message(stop_reason: str = "end_turn", text: str = '{"answer": "ok"}') -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 12,
                "output_tokens": 7,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 0,
            },
        },
    )


async def call(llm: AnthropicLlm, *, fallbacks: bool) -> Any:
    return await llm.create(
        model="claude-sonnet-5-5",
        system="SYSTEM",
        user="[PERSON_1]さんの見積",
        schema=SCHEMA,
        max_tokens=100,
        effort="medium",
        fallbacks=fallbacks,
    )


async def test_request_shape_with_fallbacks() -> None:
    llm, seen = make(lambda _: message())
    resp = await call(llm, fallbacks=True)
    assert (resp.text, resp.model, resp.stop_reason) == (
        '{"answer": "ok"}',
        "claude-sonnet-5-5",
        "end_turn",
    )
    assert (resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cache_read_tokens) == (
        12,
        7,
        3,
    )
    (req,) = seen
    body = json.loads(req.content)
    assert req.url.path == "/v1/messages"
    assert "server-side-fallback-2026-07-01" in req.headers["anthropic-beta"]
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert body["system"] == "SYSTEM"
    assert body["messages"] == [{"role": "user", "content": "[PERSON_1]さんの見積"}]


async def test_request_without_fallbacks() -> None:
    llm, seen = make(lambda _: message(stop_reason="refusal", text=""))
    resp = await call(llm, fallbacks=False)
    assert resp.stop_reason == "refusal"
    body = json.loads(seen[0].content)
    assert "fallbacks" not in body
    assert "anthropic-beta" not in seen[0].headers


@pytest.mark.parametrize(
    ("status", "code", "http_status"),
    [
        (401, "ai_auth_failed", 503),
        (429, "ai_unavailable", 503),
        (400, "ai_bad_request", 502),
        (529, "ai_unavailable", 503),
    ],
)
async def test_api_errors(status: int, code: str, http_status: int) -> None:
    llm, _ = make(
        lambda _: httpx2.Response(
            status, json={"type": "error", "error": {"type": "x", "message": "SECRET-ERR"}}
        )
    )
    with pytest.raises(LlmError) as exc:
        await call(llm, fallbacks=False)
    assert (exc.value.code, exc.value.http_status) == (code, http_status)
    assert "SECRET-ERR" not in exc.value.message


async def test_connection_error() -> None:
    def boom(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    llm, _ = make(boom)
    with pytest.raises(LlmError) as exc:
        await call(llm, fallbacks=False)
    assert exc.value.code == "ai_unavailable"


def test_base_url_pinned_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    # ANTHROPIC_BASE_URL (vd. gateway của máy dev) không được dùng: NDA chỉ cho phép Claude API.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://gateway.example.com")
    monkeypatch.delenv("PMM_ANTHROPIC_BASE_URL", raising=False)
    llm = AnthropicLlm()
    assert str(llm._client.base_url).rstrip("/") == "https://api.anthropic.com"
    monkeypatch.setenv("PMM_ANTHROPIC_BASE_URL", "https://eu.api.anthropic.com")
    assert anthropic_base_url() == "https://eu.api.anthropic.com"
    for bad in ("http://api.anthropic.com", "https://anthropic.com.evil.example", "https://x.io"):
        monkeypatch.setenv("PMM_ANTHROPIC_BASE_URL", bad)
        with pytest.raises(ValueError):
            anthropic_base_url()
