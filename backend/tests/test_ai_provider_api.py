# ruff: noqa: F811
"""API nhà cung cấp AI tùy chỉnh (spec 24): lưu key mã hóa, không lộ key, hỏi đáp qua provider.

Gateway thật được thay bằng FakeLlm (build_llm bị monkeypatch); client OpenAI-compatible đã có
test riêng trong test_ai_provider.py.
"""

from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text

from app.ai import provider
from app.ai.client import LlmResponse, LlmUsage
from app.main import create_app
from tests.mlfake import FakeLlm, FakeMl
from tests.test_ai_api import (  # noqa: F401  (fixture dùng chung)
    ANSWER,
    api,
    ask,
    client,
    fresh_people_cache,
    llm,
    ml,
    plan,
    world,
)
from tests.test_ai_api import app as app  # noqa: F401
from tests.webutil import CSRF, login

KEY = "sk-or-v1-abcdefghijklmnop1234"
BODY = {"protocol": "openai", "base_url": "https://openrouter.ai/api/v1", "api_key": KEY}
MODELS = {"question_analysis": "google/gemini-flash", "answer": "anthropic/claude-sonnet-5-5"}
OK_JSON = LlmResponse('{"ok": true}', "m", "end_turn", LlmUsage(5, 2))


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test không gọi DNS thật: mọi host phân giải về một địa chỉ công cộng."""

    async def fake(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(provider, "resolve_ips", fake)


@pytest.fixture
def secret_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PMM_SECRET_KEY", key)
    return key


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeLlm:
    """Client của provider tùy chỉnh (khác client môi trường); ghi lại cấu hình được dựng."""
    fake = FakeLlm()
    built: list[provider.ProviderConfig] = []

    def build(config: provider.ProviderConfig) -> FakeLlm:
        built.append(config)
        return fake

    monkeypatch.setattr(provider, "build_llm", build)
    fake.built = built  # type: ignore[attr-defined]
    return fake


async def put_provider(client: AsyncClient, **kw: object):
    return await client.put("/api/v1/ai/provider", json={**BODY, **kw}, headers=CSRF)


async def set_models(client: AsyncClient, models: Mapping[str, str | None] = MODELS):
    return await client.put("/api/v1/ai/settings", json={"models": models}, headers=CSRF)


async def test_save_requires_secret_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, owner_engine: Engine
) -> None:
    monkeypatch.delenv("PMM_SECRET_KEY", raising=False)
    info = (await client.get("/api/v1/ai/provider")).json()
    assert info["secret_key_ready"] is False and info["custom"] is None
    r = await put_provider(client)
    assert r.status_code == 503 and r.json()["code"] == "secret_key_missing"
    assert KEY not in r.text
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from user_ai_provider")) == 0


async def test_key_is_encrypted_and_never_returned(
    client: AsyncClient, secret_key: str, owner_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    r = await put_provider(client)
    assert r.status_code == 200
    assert r.json()["custom"] == {
        "protocol": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_set": True,
        "api_key_hint": KEY[-4:],
    }
    get = await client.get("/api/v1/ai/provider")
    assert get.json()["custom"] == r.json()["custom"] and get.json()["secret_key_ready"] is True
    for resp in (r, get, await client.get("/api/v1/ai/settings")):
        assert KEY not in resp.text
    assert KEY not in caplog.text
    with owner_engine.connect() as conn:
        stored = conn.scalar(text("select api_key_enc from user_ai_provider"))
    assert stored and KEY not in stored


async def test_blank_key_keeps_existing_and_requires_one_first(
    client: AsyncClient, secret_key: str, gateway: FakeLlm, world: SimpleNamespace
) -> None:
    r = await put_provider(client, api_key=None)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "api_key"]
    assert (await put_provider(client)).status_code == 200
    r = await put_provider(client, api_key="", base_url="https://other.example/v1")
    assert r.status_code == 200 and r.json()["custom"]["api_key_hint"] == KEY[-4:]
    await set_models(client)
    gateway.responses = [plan(), ANSWER]
    assert (await ask(client)).status_code == 200
    assert gateway.built[-1].api_key == KEY  # type: ignore[attr-defined]
    assert gateway.built[-1].base_url == "https://other.example/v1"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "http://openrouter.ai/v1"),
        ("base_url", "https://169.254.169.254/v1"),
        ("base_url", "https://u:p@openrouter.ai/v1"),
        ("api_key", "has space"),
        ("api_key", "x" * 501),
        ("protocol", "gemini"),
    ],
)
async def test_invalid_provider_is_422_without_echo(
    client: AsyncClient, secret_key: str, field: str, value: str
) -> None:
    r = await put_provider(client, **{field: value})
    assert r.status_code == 422
    assert "has space" not in r.text and "u:p" not in r.text


async def test_provider_auth_and_csrf(app: FastAPI, client: AsyncClient, secret_key: str) -> None:
    assert (await client.put("/api/v1/ai/provider", json=BODY)).status_code == 403
    assert (await client.delete("/api/v1/ai/provider")).status_code == 403
    assert (await client.post("/api/v1/ai/provider/test")).status_code == 403
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/ai/provider")).status_code == 401
        assert (await anon.put("/api/v1/ai/provider", json=BODY, headers=CSRF)).status_code == 401
        assert (await anon.post("/api/v1/ai/provider/test", headers=CSRF)).status_code == 401
        assert (await anon.delete("/api/v1/ai/provider", headers=CSRF)).status_code == 401


async def test_ask_uses_custom_provider_and_models(
    client: AsyncClient,
    secret_key: str,
    gateway: FakeLlm,
    llm: FakeLlm,
    owner_engine: Engine,
    world: SimpleNamespace,
) -> None:
    assert (await put_provider(client)).status_code == 200
    r = await set_models(client)
    assert r.status_code == 200 and r.json()["custom_provider"] is True
    assert {t["task"]: t["model"] for t in r.json()["tasks"]} == {**MODELS, "translate": None}
    gateway.responses = [plan(), ANSWER]
    resp = await ask(client)
    assert resp.status_code == 200
    assert llm.requests == []  # client môi trường không được dùng
    assert [(q["model"], q["fallbacks"]) for q in gateway.requests] == [
        (MODELS["question_analysis"], False),
        (MODELS["answer"], False),
    ]
    # Che thông tin vẫn bắt buộc với provider ngoài.
    sent = gateway.sent_text()
    for secret in ("田中", "tanaka@", "1,250,000", "CT-2026-0042", "03-0000-0001"):
        assert secret not in sent
    with owner_engine.connect() as conn:
        models = [r[0] for r in conn.execute(text("select model from ai_calls order by id"))]
    assert models == list(MODELS.values())
    usage = (await client.get("/api/v1/ai/usage")).json()
    assert {r["model"]: r["estimated_usd"] for r in usage["rows"]}[
        MODELS["question_analysis"]
    ] is None


async def test_ask_without_models_is_503(
    client: AsyncClient, secret_key: str, gateway: FakeLlm, world: SimpleNamespace
) -> None:
    await put_provider(client)
    await set_models(client, {"answer": MODELS["answer"]})
    r = await ask(client)
    assert r.status_code == 503 and r.json()["code"] == "ai_model_missing"
    assert gateway.requests == []


async def test_delete_returns_to_environment_client(
    client: AsyncClient, secret_key: str, gateway: FakeLlm, llm: FakeLlm, world: SimpleNamespace
) -> None:
    await put_provider(client)
    await set_models(client)
    r = await client.delete("/api/v1/ai/provider", headers=CSRF)
    assert r.status_code == 200 and r.json()["custom"] is None
    llm.responses = [plan(), ANSWER]
    assert (await ask(client)).status_code == 200
    assert gateway.requests == [] and len(llm.requests) == 2
    assert (await client.get("/api/v1/ai/settings")).json()["custom_provider"] is False


async def test_wrong_secret_key_does_not_crash(
    client: AsyncClient, secret_key: str, monkeypatch: pytest.MonkeyPatch, world: SimpleNamespace
) -> None:
    await put_provider(client)
    monkeypatch.setenv("PMM_SECRET_KEY", Fernet.generate_key().decode())
    info = (await client.get("/api/v1/ai/provider")).json()
    assert info["custom"]["api_key_set"] is False and info["custom"]["api_key_hint"] is None
    r = await ask(client)
    assert r.status_code == 503 and r.json()["code"] == "ai_auth_failed"


async def test_provider_test_endpoint(
    client: AsyncClient, secret_key: str, gateway: FakeLlm, owner_engine: Engine
) -> None:
    r = await client.post("/api/v1/ai/provider/test", headers=CSRF)
    assert r.status_code == 503 and r.json()["code"] == "ai_not_configured"
    await put_provider(client)
    r = await client.post("/api/v1/ai/provider/test", headers=CSRF)
    assert r.status_code == 503 and r.json()["code"] == "ai_model_missing"
    await set_models(client)
    gateway.responses = [OK_JSON]
    r = await client.post("/api/v1/ai/provider/test", headers=CSRF)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["model"] == "m"
    assert "SECRET" not in gateway.sent_text()
    with owner_engine.connect() as conn:
        rows = conn.execute(text("select task, status, input_tokens from ai_calls")).all()
    assert [tuple(x) for x in rows] == [("provider_test", "ok", 5)]


async def test_other_user_cannot_see_provider(
    client: AsyncClient,
    secret_key: str,
    api,
    world: SimpleNamespace,  # noqa: ANN001
) -> None:
    await put_provider(client)
    await set_models(client)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(api, ml=FakeMl(), llm=FakeLlm(), web_dist=None)),
        base_url="http://test",
    ) as cb:
        await login(cb, "b@example.com")
        assert (await cb.get("/api/v1/ai/provider")).json()["custom"] is None
        settings = (await cb.get("/api/v1/ai/settings")).json()
        assert settings["custom_provider"] is False
        assert [t["model"] for t in settings["tasks"]] == [None, None, None]


@pytest.mark.parametrize(
    "models",
    [{"nope": "x"}, {"answer": "x" * 201}, {"answer": "a\nb"}],
)
async def test_models_validation(client: AsyncClient, models: dict[str, str]) -> None:
    assert (await set_models(client, models)).status_code == 422


async def test_clear_model_with_blank(client: AsyncClient, secret_key: str) -> None:
    await set_models(client)
    r = await set_models(client, {"answer": "  "})
    assert {t["task"]: t["model"] for t in r.json()["tasks"]}["answer"] is None
    # Chỉ có model, chưa chọn cấp: cấp vẫn là mặc định của tác vụ.
    assert {t["task"]: t["tier"] for t in r.json()["tasks"]} == {
        "question_analysis": "haiku",
        "answer": "sonnet",
        "translate": "sonnet",
    }


async def test_host_resolving_to_private_address_is_422(
    client: AsyncClient, secret_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.7"]

    monkeypatch.setattr(provider, "resolve_ips", private)
    r = await put_provider(client)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "base_url"]


async def test_ask_refuses_when_stored_host_turns_private(
    client: AsyncClient,
    secret_key: str,
    gateway: FakeLlm,
    monkeypatch: pytest.MonkeyPatch,
    world: SimpleNamespace,
) -> None:
    await put_provider(client)
    await set_models(client)

    async def private(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(provider, "resolve_ips", private)
    r = await ask(client)
    assert r.status_code == 502 and r.json()["code"] == "ai_bad_request"
    assert gateway.requests == []
