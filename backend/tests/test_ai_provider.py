"""Nhà cung cấp AI tùy chỉnh (spec 24): mã hóa key, kiểm tra URL, client OpenAI-compatible.

Không gọi gateway thật: dùng httpx.MockTransport.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
import pytest
from cryptography.fernet import Fernet

from app.ai import provider
from app.ai import secrets as ai_secrets
from app.ai.client import TIMEOUT_MESSAGE, TIMEOUT_SECONDS, LlmError
from app.ai.provider import OpenAiLlm, is_public_host, reject_link_local, validate_base_url
from tests.test_ai_client import message

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
KEY = "sk-or-test-1234567890abcd"


@pytest.fixture
def secret_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PMM_SECRET_KEY", key)
    return key


# --- mã hóa key (mục 5) ---


def test_encrypt_roundtrip_and_ciphertext_hides_plaintext(secret_key: str) -> None:
    token = ai_secrets.encrypt(KEY)
    assert KEY not in token
    assert ai_secrets.decrypt(token) == KEY


def test_decrypt_with_other_key_is_none_not_crash(
    secret_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = ai_secrets.encrypt(KEY)
    monkeypatch.setenv("PMM_SECRET_KEY", Fernet.generate_key().decode())
    assert ai_secrets.decrypt(token) is None


def test_missing_or_bad_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PMM_SECRET_KEY", raising=False)
    assert not ai_secrets.secret_key_ready()
    with pytest.raises(ai_secrets.SecretKeyMissing):
        ai_secrets.encrypt(KEY)
    assert ai_secrets.decrypt("whatever") is None
    monkeypatch.setenv("PMM_SECRET_KEY", "not-a-fernet-key")
    assert not ai_secrets.secret_key_ready()


# --- kiểm tra URL (mục 6) ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
        ("https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1"),
        ("HTTPS://OpenRouter.AI/api/v1", "https://openrouter.ai/api/v1"),
        ("http://127.0.0.1:20128/v1", "http://127.0.0.1:20128/v1"),
        ("http://localhost:20128/v1", "http://localhost:20128/v1"),
        ("http://[::1]:8080/v1", "http://[::1]:8080/v1"),
        ("http://10.0.0.5:20128/v1", "http://10.0.0.5:20128/v1"),
        ("http://192.168.1.2", "http://192.168.1.2"),
        ("http://gateway:8080", "http://gateway:8080"),
        ("https://gateway.internal/v1", "https://gateway.internal/v1"),
        ("https://localhost", "https://localhost"),
        ("http://printer.local/v1", "http://printer.local/v1"),
        ("https://gateway.internal./v1", "https://gateway.internal/v1"),  # dấu chấm cuối (FQDN)
    ],
)
def test_url_accepted(raw: str, expected: str) -> None:
    assert validate_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ftp://x.example.com",
        "http://u:p@host",
        "https://user:pw@openrouter.ai/v1",
        "https://user@openrouter.ai/v1",
        "http://host?x=1",
        "https://openrouter.ai/v1?token=abc",
        "http://host#a",
        "https://openrouter.ai/v1#frag",
        "https://openrouter.ai:0/v1",
        "https://openrouter.ai:70000/v1",
        "https:///v1",
        "not a url",
        "http://2130706433",  # 127.0.0.1 viết dạng số nguyên
        "https://2130706433/v1",
        "http://0x7f.1",
        "https://0x7f.1/v1",
        "http://169.254.169.254",
        "https://169.254.169.254/latest/meta-data",
        "http://[fe80::1]",
        "https://[fe80::1]/v1",
        "http://[::ffff:169.254.169.254]",
        # Dấu chấm cuối không được che IP dạng số hay link-local (sửa sau review).
        "http://2852039166.",
        "http://0xa9fea9fe.",
        "http://169.254.169.254.",
        "http://169.254.169.254../v1",
        "http://...",
        # Ký tự điều khiển, khoảng trắng: urlsplit tự bỏ \t \r \n nên phải chặn trước khi tách.
        "http://host/\x01x",
        "http://ho\x01st/v1",
        "http://host/a b",
        "http://ho st/v1",
        "http://host/\tx",
        "http://ho\nst/v1",
        "http://host/\x7fx",
        "http://host/\u00a0x",
    ],
)
def test_url_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_base_url(raw)


# --- client OpenAI-compatible (mục 3) ---


def completion(
    content: str | None = '{"answer": "ok"}', finish: str = "stop", **extra: Any
) -> httpx.Response:
    body = {
        "model": "anthropic/claude-sonnet-5-5",
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        **extra,
    }
    return httpx.Response(200, json=body)


def make(handler: Any, retries: int = 0) -> tuple[OpenAiLlm, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    llm = OpenAiLlm(
        base_url="https://gateway.example/v1",
        api_key=KEY,
        transport=httpx.MockTransport(record),
        max_retries=retries,
    )
    return llm, seen


async def create(llm: OpenAiLlm) -> Any:
    return await llm.create(
        model="anthropic/claude-sonnet-5-5",
        system="SYS",
        user="USER",
        schema=SCHEMA,
        max_tokens=100,
        effort="low",
    )


async def test_json_schema_request_and_response() -> None:
    llm, seen = make(lambda r: completion())
    resp = await create(llm)
    assert resp.text == '{"answer": "ok"}'
    assert resp.stop_reason == "end_turn"
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (11, 3)
    assert resp.model == "anthropic/claude-sonnet-5-5"
    req = seen[0]
    assert str(req.url) == "https://gateway.example/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert set(req.headers) >= {"authorization", "content-type"}
    assert "referer" not in req.headers and "http-referer" not in req.headers
    body = json.loads(req.content)
    assert body["model"] == "anthropic/claude-sonnet-5-5"
    assert body["max_tokens"] == 100
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["response_format"]["json_schema"]["strict"] is True


async def test_falls_back_to_json_object_on_400() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return completion()

    llm, seen = make(handler)
    resp = await create(llm)
    assert resp.text == '{"answer": "ok"}'
    assert len(seen) == 2
    second = json.loads(seen[1].content)
    assert second["response_format"] == {"type": "json_object"}
    assert json.dumps(SCHEMA) in second["messages"][0]["content"]
    assert second["messages"][0]["content"].startswith("SYS")


async def test_second_400_is_bad_request() -> None:
    llm, seen = make(lambda r: httpx.Response(400, json={"error": "SECRET-ECHO-MAIL-TEXT"}))
    with pytest.raises(LlmError) as e:
        await create(llm)
    assert e.value.code == "ai_bad_request"
    assert "SECRET-ECHO-MAIL-TEXT" not in e.value.message
    assert len(seen) == 2


@pytest.mark.parametrize(
    ("finish", "stop_reason"),
    [("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "refusal")],
)
async def test_finish_reason_mapping(finish: str, stop_reason: str) -> None:
    llm, _ = make(lambda r: completion(finish=finish))
    assert (await create(llm)).stop_reason == stop_reason


@pytest.mark.parametrize(
    ("status", "code", "http"),
    [
        (401, "ai_auth_failed", 503),
        (403, "ai_auth_failed", 503),
        (429, "ai_unavailable", 503),
        (500, "ai_unavailable", 503),
        (503, "ai_unavailable", 503),
        (404, "ai_bad_request", 502),
    ],
)
async def test_http_errors_do_not_echo_body(status: int, code: str, http: int) -> None:
    llm, _ = make(lambda r: httpx.Response(status, text=f"ECHO {KEY} MAIL-BODY"))
    with pytest.raises(LlmError) as e:
        await create(llm)
    assert (e.value.code, e.value.http_status) == (code, http)
    assert "MAIL-BODY" not in e.value.message and KEY not in e.value.message


@pytest.mark.parametrize("status", [429, 502])
async def test_overload_log_has_status_code_only(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    llm, _ = make(lambda r: httpx.Response(status, text=f"ECHO {KEY} MAIL-BODY"))
    with caplog.at_level("WARNING", logger="app.ai"), pytest.raises(LlmError) as e:
        await create(llm)
    assert f"type=Status{status}" in caplog.text
    assert f"HTTP {status}" in e.value.message
    assert "gateway.example" in e.value.message and "anthropic/claude-sonnet-5-5" in e.value.message
    assert "MAIL-BODY" not in e.value.message and KEY not in e.value.message
    assert "MAIL-BODY" not in caplog.text and KEY not in caplog.text


async def test_network_error_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    llm, _ = make(boom)
    with pytest.raises(LlmError) as e:
        await create(llm)
    assert e.value.code == "ai_unavailable"


class SlowBody(httpx.AsyncByteStream):
    """Gateway gửi header rồi im: hết giờ giữa lúc đọc body."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{"
        raise httpx.ReadTimeout("SECRET-TIMEOUT-TEXT")


async def test_read_timeout_is_ai_timeout_without_retry(caplog: pytest.LogCaptureFixture) -> None:
    """Mỗi lần thử tốn tới 120 giây tải của gateway: hết giờ đọc phản hồi thì không thử lại."""

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("SECRET-TIMEOUT-TEXT", request=request)

    llm, seen = make(slow, retries=2)
    with caplog.at_level("WARNING", logger="app.ai"), pytest.raises(LlmError) as e:
        await create(llm)
    assert (e.value.code, e.value.http_status) == ("ai_timeout", 503)
    assert e.value.message == TIMEOUT_MESSAGE
    assert len(seen) == 1
    assert "type=ReadTimeout" in caplog.text and "SECRET" not in caplog.text


async def test_read_timeout_in_body_is_ai_timeout_without_retry() -> None:
    llm, seen = make(lambda r: httpx.Response(200, stream=SlowBody()), retries=2)
    with pytest.raises(LlmError) as e:
        await create(llm)
    assert e.value.code == "ai_timeout"
    assert len(seen) == 1


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout])
async def test_connection_errors_stay_unavailable_and_retry(
    error: type[httpx.TransportError],
) -> None:
    """Gateway tắt: dừng người dùng là đúng, không phải lỗi của một mail."""

    def down(request: httpx.Request) -> httpx.Response:
        raise error("SECRET-TIMEOUT-TEXT", request=request)

    llm, seen = make(down, retries=2)
    with pytest.raises(LlmError) as e:
        await create(llm)
    assert e.value.code == "ai_unavailable"
    assert "SECRET" not in e.value.message
    assert len(seen) == 3


async def test_redirect_is_not_followed() -> None:
    llm, seen = make(lambda r: httpx.Response(302, headers={"location": "https://evil.example/"}))
    with pytest.raises(LlmError):
        await create(llm)
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={}),
        httpx.Response(200, text="not json"),
        completion(content=None),
    ],
)
async def test_odd_200_responses(response: httpx.Response) -> None:
    llm, _ = make(lambda r: response)
    try:
        resp = await create(llm)
    except LlmError as e:
        assert e.code in {"ai_bad_output", "ai_unavailable"}
    else:
        assert not resp.text  # để call_task báo ai_bad_output


def body_with_usage(usage: object) -> str:
    return json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "usage": usage}
    )


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": "SECRET-USAGE"},
        {"prompt_tokens": 1, "completion_tokens": "SECRET-USAGE"},
        "SECRET-USAGE",
        ["SECRET-USAGE"],
        {"prompt_tokens": float("inf")},
    ],
)
async def test_odd_usage_is_bad_output_without_echo(
    usage: object, caplog: pytest.LogCaptureFixture
) -> None:
    llm, _ = make(lambda r: httpx.Response(200, content=body_with_usage(usage)))
    with caplog.at_level("DEBUG"), pytest.raises(LlmError) as e:
        await create(llm)
    assert e.value.code == "ai_bad_output"
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert "SECRET" not in e.value.message and "SECRET" not in caplog.text


@pytest.mark.parametrize(
    "served", ["a\nb", "m\r\nai user=9 status=ok", "\x1b[2Jm", "x" * 201, "", 123, ["m"], None]
)
async def test_odd_model_name_falls_back_to_requested(served: object) -> None:
    llm, _ = make(lambda r: completion(model=served))
    assert (await create(llm)).model == "anthropic/claude-sonnet-5-5"


@pytest.mark.parametrize("served", ["gw/model-2", "x" * 200, "モデル 1"])
async def test_model_name_of_gateway_is_kept(served: str) -> None:
    llm, _ = make(lambda r: completion(model=served))
    assert (await create(llm)).model == served


async def test_oversized_response_is_rejected() -> None:
    big = httpx.Response(200, content=b"x" * (provider.MAX_RESPONSE_BYTES + 1))
    llm, _ = make(lambda r: big)
    with pytest.raises(LlmError) as e:
        await create(llm)
    assert e.value.code == "ai_bad_output"


def fake_resolve(monkeypatch: pytest.MonkeyPatch, ips: list[str] | type[OSError]) -> None:
    async def fake(host: str, port: int) -> list[str]:
        if isinstance(ips, list):
            return ips
        raise ips("dns")

    monkeypatch.setattr(provider, "resolve_ips", fake)


@pytest.mark.parametrize(
    "ips",
    [["10.0.0.1", "169.254.169.254"], ["fe80::1%eth0"], ["::ffff:169.254.169.254"]],
)
async def test_reject_link_local_when_host_resolves_there(
    monkeypatch: pytest.MonkeyPatch, ips: list[str]
) -> None:
    fake_resolve(monkeypatch, ips)
    with pytest.raises(ValueError):
        await reject_link_local("https://gateway.example/v1")


@pytest.mark.parametrize("ips", [["10.0.0.1"], ["127.0.0.1"], ["93.184.216.34"], []])
async def test_reject_link_local_allows_private_and_public(
    monkeypatch: pytest.MonkeyPatch, ips: list[str]
) -> None:
    fake_resolve(monkeypatch, ips)
    await reject_link_local("http://gateway:8080/v1")


async def test_reject_link_local_ignores_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resolve(monkeypatch, OSError)
    await reject_link_local("https://gateway.example/v1")


@pytest.mark.parametrize(
    ("ips", "expected"),
    [
        (["93.184.216.34"], True),
        (["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"], True),
        (["10.0.0.5", "93.184.216.34"], True),
        (["10.0.0.5"], False),
        (["127.0.0.1"], False),
        (["fe80::1%eth0"], False),
        ([], False),
    ],
)
async def test_is_public_host(
    monkeypatch: pytest.MonkeyPatch, ips: list[str], expected: bool
) -> None:
    fake_resolve(monkeypatch, ips)
    assert await is_public_host("https://gateway.example/v1") is expected


async def test_is_public_host_false_when_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resolve(monkeypatch, OSError)
    assert await is_public_host("https://gateway.example/v1") is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [("http://93.184.216.34:8080/v1", True), ("http://10.0.0.5:20128/v1", False)],
)
async def test_is_public_host_literal_ip_skips_dns(
    monkeypatch: pytest.MonkeyPatch, url: str, expected: bool
) -> None:
    async def boom(host: str, port: int) -> list[str]:
        raise AssertionError("IP thật không cần phân giải DNS")

    monkeypatch.setattr(provider, "resolve_ips", boom)
    assert await is_public_host(url) is expected


async def test_openai_llm_posts_to_lan_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return completion()

    llm = OpenAiLlm(
        base_url=validate_base_url("http://10.0.0.5:20128/v1"),
        api_key=KEY,
        transport=httpx.MockTransport(handler),
    )
    await create(llm)
    assert seen == ["http://10.0.0.5:20128/v1/chat/completions"]


@pytest.fixture
def proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proxy trong môi trường: mail gốc và API key không được đi qua, kể cả tới địa chỉ LAN."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")


async def test_openai_client_ignores_env_proxy(proxy_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return completion()

    llm: Any = provider.build_llm(
        provider.ProviderConfig("openai", "http://10.0.0.5:20128/v1", KEY)
    )
    http = llm._http
    assert http.trust_env is False
    assert http._mounts == {}
    http._transport = httpx.MockTransport(handler)
    await create(llm)
    assert seen == ["http://10.0.0.5:20128/v1/chat/completions"]


async def test_anthropic_client_ignores_env_proxy(proxy_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return message()

    llm: Any = provider.build_llm(
        provider.ProviderConfig("anthropic", "http://10.0.0.5:20128", KEY)
    )
    http = llm._client._client
    assert http.trust_env is False
    assert http._mounts == {}
    assert http.follow_redirects is False
    assert http.timeout == httpx2.Timeout(TIMEOUT_SECONDS)
    http._transport = httpx2.MockTransport(handler)
    await create(llm)
    assert seen == ["http://10.0.0.5:20128/v1/messages"]


async def test_anthropic_protocol_does_not_follow_redirects() -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(307, headers={"location": "http://127.0.0.1:8001/embed"})

    cfg = provider.ProviderConfig("anthropic", "https://gateway.example", KEY)
    llm = provider.build_llm(cfg)
    inner = llm._client  # type: ignore[attr-defined]
    assert inner._client.follow_redirects is False
    inner._client._transport = httpx.MockTransport(handler)  # type: ignore[assignment]
    with pytest.raises(LlmError):
        await llm.create(
            model="m",
            system="s",
            user="u",
            schema=SCHEMA,
            max_tokens=10,
            effort="low",
        )
    assert all(u.startswith("https://gateway.example") for u in hits)


def test_key_hint_hides_short_keys() -> None:
    assert provider.key_hint("sk-or-v1-abcdefgh1234") == "1234"
    assert provider.key_hint("short-key") is None
