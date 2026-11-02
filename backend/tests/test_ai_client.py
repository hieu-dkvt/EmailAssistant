"""AnthropicLlm với transport HTTP giả: request SDK gửi đi đúng dạng, đọc đúng response và lỗi.

Không gọi Anthropic thật (không cần khóa API).
"""

import json
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from app.ai import provider
from app.ai.client import TIMEOUT_MESSAGE, AnthropicLlm, LlmError

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


def message(
    stop_reason: str = "end_turn",
    text: str = '{"answer": "ok"}',
    model: str = "claude-sonnet-5-5",
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
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


async def call(llm: AnthropicLlm) -> Any:
    return await llm.create(
        model="claude-sonnet-5-5",
        system="SYSTEM",
        user="[PERSON_1]さんの見積",
        schema=SCHEMA,
        max_tokens=100,
        effort="medium",
    )


async def test_request_shape() -> None:
    llm, seen = make(lambda _: message())
    resp = await call(llm)
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
    assert "anthropic-beta" not in req.headers
    assert "fallbacks" not in body
    assert body["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert body["system"] == "SYSTEM"
    assert body["messages"] == [{"role": "user", "content": "[PERSON_1]さんの見積"}]


async def test_refusal_is_returned_not_raised() -> None:
    llm, _ = make(lambda _: message(stop_reason="refusal", text=""))
    resp = await call(llm)
    assert resp.stop_reason == "refusal"


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
        await call(llm)
    assert (exc.value.code, exc.value.http_status) == (code, http_status)
    assert "SECRET-ERR" not in exc.value.message


async def test_connection_error() -> None:
    def boom(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    llm, _ = make(boom)
    with pytest.raises(LlmError) as exc:
        await call(llm)
    assert exc.value.code == "ai_unavailable"


def built(
    handler: Any, base_url: str = "http://gateway.example"
) -> tuple[Any, list[httpx2.Request]]:
    """Client đúng như chạy thật (_anthropic_llm), chỉ thay transport bằng handler."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    llm: Any = provider._anthropic_llm(provider.ProviderConfig("anthropic", base_url, "test-key"))
    llm._client._client._transport = httpx2.MockTransport(record)
    return llm, seen


async def test_sdk_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK tự đọc các biến này: chỉ key và URL người dùng nhập được dùng (spec 28 mục 4).

    pyproject chỉ ghim anthropic>=1,<2 nên test này canh khi nâng SDK.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.example")
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS", "X-Env-Leak: SECRET-ENV\nAuthorization: Bearer env-token"
    )
    llm, seen = built(lambda _: message())
    await call(llm)
    (req,) = seen
    assert str(req.url) == "http://gateway.example/v1/messages"
    assert req.headers["x-api-key"] == "test-key"
    assert "authorization" not in req.headers and "x-env-leak" not in req.headers
    assert "SECRET-ENV" not in str(req.headers.raw)
    inner = llm._client
    assert inner.auth_token is None and inner.credentials is None
    assert inner.auth_headers == {"X-Api-Key": "test-key"}
    assert "X-Env-Leak" not in inner.default_headers


async def test_read_timeout_is_ai_timeout_without_sdk_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("SECRET-TIMEOUT-TEXT", request=request)

    llm, seen = built(slow)
    with caplog.at_level("WARNING", logger="app.ai"), pytest.raises(LlmError) as exc:
        await call(llm)
    assert (exc.value.code, exc.value.http_status) == ("ai_timeout", 503)
    assert exc.value.message == TIMEOUT_MESSAGE
    assert len(seen) == 1  # SDK mặc định thử lại mọi APITimeoutError
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize("error", [httpx2.ConnectTimeout, httpx2.ConnectError, httpx2.PoolTimeout])
async def test_connection_errors_stay_unavailable_and_sdk_retries(
    error: type[httpx2.TransportError],
) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise error("SECRET-TIMEOUT-TEXT", request=request)

    llm, seen = built(down)
    with pytest.raises(LlmError) as exc:
        await call(llm)
    assert exc.value.code == "ai_unavailable"
    assert len(seen) == provider.MAX_RETRIES + 1


async def test_overload_is_still_retried_by_sdk() -> None:
    replies = [httpx2.Response(529, json={"type": "error", "error": {}}), message()]
    llm, seen = built(lambda _: replies.pop(0))
    assert (await call(llm)).text == '{"answer": "ok"}'
    assert len(seen) == 2


@pytest.mark.parametrize("served", ["a\nb", "\x1b[2Jm", "x" * 201])
async def test_odd_model_name_falls_back_to_requested(served: str) -> None:
    llm, _ = make(lambda _: message(model=served))
    assert (await call(llm)).model == "claude-sonnet-5-5"


async def test_model_name_of_gateway_is_kept() -> None:
    llm, _ = make(lambda _: message(model="gw/model-2"))
    assert (await call(llm)).model == "gw/model-2"
