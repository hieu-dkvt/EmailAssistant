"""Hỏi đáp AI (spec 21) và cài đặt model (spec 22) với AI giả và ml-service giả.

Kiểm tra quan trọng nhất: request gửi AI không chứa mail của người khác (RLS). Từ spec 28 text
gửi AI là text gốc, không che (xem test_ask_text.py).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import embedding
from app.ai import ask as ask_module
from app.ai import provider
from app.ai.client import TIMEOUT_MESSAGE, LlmError, LlmResponse, LlmUsage
from app.ai.prompts import AnswerOut, QuestionPlan, TranslationOut
from app.db.session import create_api_engine
from app.main import create_app
from app.processing import process_user
from tests.dbutil import add_source, set_ai_models, url_for_test_db
from tests.mlfake import FakeLlm, FakeMl
from tests.test_web_api import put_mail
from tests.webutil import CSRF, add_web_user, login


@pytest.fixture
async def world(owner_engine: Engine, worker: AsyncEngine, clean: None) -> SimpleNamespace:
    a = add_web_user(owner_engine, "a@example.com")
    b = add_web_user(owner_engine, "b@example.com")
    with owner_engine.begin() as conn:
        fa = add_source(conn, a, "ImapMail/imap.example.com/INBOX")
        fb = add_source(conn, b, "ImapMail/imap.example.com/INBOX")
        for u in (a, b):
            set_ai_models(conn, u)
        est = put_mail(
            conn,
            a,
            "est@a.example.jp",
            [fa],
            subject="【サンプルA】お見積りの送付",
            body=(
                "Trần様\n\nお世話になっております。田中健二です。\n"
                "お見積りの金額は1,250,000円、契約番号: CT-2026-0042 です。\n"
                "担当の高橋も同席します。ご不明点は 03-0000-0001 までご連絡ください。\n\n"
                "--\n株式会社サンプルA\nシステム開発部 田中 健二\nTEL: 03-0000-0001"
            ),
            from_name="田中 健二",
            day=1,
        )
        other = put_mail(
            conn, a, "news@a.example.jp", [fa], subject="お知らせ", body="休業日のお知らせ", day=2
        )
        b_est = put_mail(
            conn,
            b,
            "b-est@b.example.jp",
            [fb],
            subject="見積",
            body="SECRET-OF-B 見積 見積 見積",
            day=1,
        )
    for u in (a, b):
        await process_user(worker, u)
        await embedding.embed_user(worker, FakeMl(), u)
    return SimpleNamespace(a=a, b=b, est=est, other=other, b_est=b_est, fa=fa)


@pytest.fixture
async def api() -> AsyncIterator[AsyncEngine]:
    engine = create_api_engine(url_for_test_db("api"))
    yield engine
    await engine.dispose()


@pytest.fixture
def ml() -> FakeMl:
    return FakeMl()


@pytest.fixture
def llm() -> FakeLlm:
    return FakeLlm()


@pytest.fixture
def app(api: AsyncEngine, ml: FakeMl, llm: FakeLlm) -> FastAPI:
    return create_app(api, ml=ml, llm=llm, web_dist=None)


@pytest.fixture
async def client(app: FastAPI, world: SimpleNamespace) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        yield c


GATEWAY_KEY = "sk-gateway-secret-1234567890"
GATEWAY_URL = "http://10.0.0.5:20128/secret-path/v1"


def chat_reply(out: BaseModel, model: str = "gw/model") -> httpx.Response:
    """Phản hồi chat/completions của gateway với nội dung là output có cấu trúc."""
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {"content": out.model_dump_json()}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        },
    )


@dataclass
class Gateway:
    """Gateway giả sau client OpenAiLlm thật: đi đủ resolver, giải mã key, parse, không có mạng."""

    client: AsyncClient
    replies: list[Any] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
async def real_gateway(
    api: AsyncEngine, ml: FakeMl, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Gateway]:
    """A đã lưu nhà cung cấp trong DB, app không có client AI giả."""

    async def lan(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    def build(config: provider.ProviderConfig) -> provider.OpenAiLlm:
        return provider.OpenAiLlm(
            base_url=config.base_url,
            api_key=config.api_key,
            transport=httpx.MockTransport(gateway.handle),
            max_retries=0,
        )

    monkeypatch.setenv("PMM_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(provider, "resolve_ips", lan)
    monkeypatch.setattr(provider, "build_llm", build)
    await provider.save_provider(api, world.a, "openai", GATEWAY_URL, GATEWAY_KEY)
    app = create_app(api, ml=ml, web_dist=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        gateway = Gateway(c)
        yield gateway


def plan(**kw: object) -> QuestionPlan:
    base: dict[str, object] = {
        "search_queries": ["見積の金額"],
        "keywords": ["見積"],
        "sender": None,
        "sent_after": None,
        "sent_before": None,
    }
    return QuestionPlan.model_validate({**base, **kw})


ANSWER = AnswerOut(
    answer="田中様(サンプルA)が1,250,000円でお見積りを送りました [1]. Liên hệ 03-0000-0001.",
    cited_sources=[1, 7],
    insufficient=False,
)


async def ask(client: AsyncClient, question: str = "Khách gửi báo giá bao nhiêu?", **kw: object):
    return await client.post("/api/v1/ask", json={"question": question, **kw}, headers=CSRF)


def ai_calls(engine: Engine) -> list[tuple[int, str, str, str, int]]:
    with engine.connect() as conn:
        return [
            tuple(r)  # type: ignore[misc]
            for r in conn.execute(
                text("select user_id, task, model, status, input_tokens from ai_calls order by id")
            )
        ]


async def test_ask_sources_calls_and_isolation(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [plan(), ANSWER]
    r = await ask(client, "田中さんの見積はいくらでしたか?")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "SECRET-OF-B" not in llm.sent_text()  # mail của B không vào request của A
    # Nguồn 7 không tồn tại bị bỏ.
    assert data["answer"] == ANSWER.answer
    assert [s["message_id"] for s in data["sources"] if s["cited"]] == [world.est]
    assert world.b_est not in [s["message_id"] for s in data["sources"]]
    # "Nội dung đã gửi AI" đúng là text trong request.
    assert [s["content"] for s in data["sent"]] == [req["user"] for req in llm.requests]
    assert [c["task"] for c in data["calls"]] == ["question_analysis", "answer"]
    # Tên model là tên người dùng nhập; không còn tham số fallbacks.
    assert [q["model"] for q in llm.requests] == ["test-model", "test-model"]
    assert all("fallbacks" not in q for q in llm.requests)
    assert ai_calls(owner_engine) == [
        (world.a, "question_analysis", "test-model", "ok", 100),
        (world.a, "answer", "test-model", "ok", 100),
    ]


async def test_plan_sender_narrows_local_search(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [plan(sender="田中", sent_after="2026-09-01"), ANSWER]
    data = (await ask(client, "田中さんは何を送りましたか")).json()
    assert data["plan"]["sender"] == "田中"
    assert data["plan"]["sent_after"] == "2026-09-01"
    assert data["plan"]["filters_relaxed"] is False
    assert [s["message_id"] for s in data["sources"]] == [world.est]


async def test_plan_filters_relaxed_when_nothing_found(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [plan(sender="nobody-at-all"), ANSWER]
    data = (await ask(client)).json()
    assert data["plan"]["filters_relaxed"] is True
    assert world.est in [s["message_id"] for s in data["sources"]]


async def test_no_sources_skips_answer_call(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [plan(search_queries=[], keywords=[])]
    data = (await ask(client, "x", folder_id=999999)).json()
    assert data["sources"] == [] and data["insufficient"] is True
    assert len(llm.requests) == 1


async def test_settings_choose_model_per_task(
    client: AsyncClient, llm: FakeLlm, owner_engine: Engine, world: SimpleNamespace
) -> None:
    data = (await client.get("/api/v1/ai/settings")).json()
    assert {t["task"]: t["model"] for t in data["tasks"]} == {
        "question_analysis": "test-model",
        "answer": "test-model",
        "translate": "test-model",
    }
    assert "tiers" not in data and "custom_provider" not in data
    assert all("tier" not in t and "default_tier" not in t for t in data["tasks"])
    r = await client.put(
        "/api/v1/ai/settings",
        json={"models": {"question_analysis": "m-analysis", "answer": "m-answer"}},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert {t["task"]: t["model"] for t in r.json()["tasks"]} == {
        "question_analysis": "m-analysis",
        "answer": "m-answer",
        "translate": "test-model",
    }
    llm.responses = [plan(), ANSWER]
    await ask(client)
    assert [q["model"] for q in llm.requests] == ["m-analysis", "m-answer"]
    # B không bị ảnh hưởng (RLS).
    async with AsyncClient(
        transport=ASGITransport(
            app=create_app(
                create_api_engine(url_for_test_db("api")), ml=FakeMl(), llm=FakeLlm(), web_dist=None
            )
        ),
        base_url="http://test",
    ) as cb:
        await login(cb, "b@example.com")
        models = {
            t["task"]: t["model"] for t in (await cb.get("/api/v1/ai/settings")).json()["tasks"]
        }
    assert set(models.values()) == {"test-model"}


async def test_clearing_model_deletes_row(
    client: AsyncClient, owner_engine: Engine, world: SimpleNamespace
) -> None:
    r = await client.put("/api/v1/ai/settings", json={"models": {"answer": None}}, headers=CSRF)
    assert r.status_code == 200
    assert {t["task"]: t["model"] for t in r.json()["tasks"]}["answer"] is None
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("select user_id, task from user_ai_settings where task = 'answer'")
        ).all()
    assert [tuple(x) for x in rows] == [(world.b, "answer")]  # dòng của B còn nguyên
    # Xóa lại khi không còn dòng: không lỗi.
    r = await client.put("/api/v1/ai/settings", json={"models": {"answer": "  "}}, headers=CSRF)
    assert r.status_code == 200


async def test_settings_rejects_tiers(client: AsyncClient) -> None:
    r = await client.put("/api/v1/ai/settings", json={"tiers": {"answer": "opus"}}, headers=CSRF)
    assert r.status_code == 422


@pytest.mark.parametrize(
    "body",
    [{"models": {"unknown_task": "x"}}, {"models": "x"}, {"models": {"answer": "x" * 201}}],
)
async def test_settings_validation(client: AsyncClient, body: dict[str, object]) -> None:
    r = await client.put("/api/v1/ai/settings", json=body, headers=CSRF)
    assert r.status_code == 422


async def test_settings_requires_csrf_and_login(app: FastAPI, client: AsyncClient) -> None:
    r = await client.put("/api/v1/ai/settings", json={"models": {}})
    assert r.status_code == 403
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/ai/settings")).status_code == 401
        assert (
            await anon.post("/api/v1/ask", json={"question": "x"}, headers=CSRF)
        ).status_code == 401


async def test_usage_this_month(
    client: AsyncClient, llm: FakeLlm, owner_engine: Engine, world: SimpleNamespace
) -> None:
    llm.responses = [plan(), ANSWER]
    await ask(client)
    with owner_engine.begin() as conn:  # dòng của B và dòng tháng trước không được tính
        conn.execute(
            text(
                "insert into ai_calls (user_id, task, model, status, input_tokens, output_tokens) "
                "values (:b, 'answer', 'other-model', 'ok', 999, 999)"
            ),
            {"b": world.b},
        )
        conn.execute(
            text(
                "insert into ai_calls (user_id, task, model, status, input_tokens, created_at) "
                "values (:a, 'answer', 'other-model', 'ok', 999, now() - interval '40 days')"
            ),
            {"a": world.a},
        )
    data = (await client.get("/api/v1/ai/usage")).json()
    assert [(r["model"], r["calls"], r["input_tokens"]) for r in data["rows"]] == [
        ("test-model", 2, 200),
    ]
    assert data["total_calls"] == 2


async def test_usage_has_no_price(client: AsyncClient, llm: FakeLlm) -> None:
    llm.responses = [plan(), ANSWER]
    await ask(client)
    data = (await client.get("/api/v1/ai/usage")).json()
    assert data["rows"] and "estimated_usd" not in data["rows"][0]
    assert "total_estimated_usd" not in data


async def test_ml_down_sends_nothing_more(client: AsyncClient, ml: FakeMl, llm: FakeLlm) -> None:
    ml.down = True  # /ask vẫn cần ml-service để embed câu hỏi
    llm.responses = [plan(), ANSWER]
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (503, "ml_unavailable")
    assert len(llm.requests) == 1  # chỉ bước phân tích; chưa có nguồn nào gửi đi


async def test_refusal_and_api_errors(
    client: AsyncClient, llm: FakeLlm, owner_engine: Engine
) -> None:
    llm.responses = [
        LlmResponse(
            text=None,
            model="claude-haiku-5-5",
            stop_reason="refusal",
            usage=LlmUsage(input_tokens=5),
        ),
    ]
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (502, "ai_refused")
    llm.responses = [LlmError("ai_unavailable", 503, "Không kết nối được AI")]
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (503, "ai_unavailable")
    llm.responses = [
        LlmResponse(
            text='{"oops": 1}', model="claude-haiku-5-5", stop_reason="end_turn", usage=LlmUsage()
        ),
    ]
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (502, "ai_bad_output")
    assert [row[3] for row in ai_calls(owner_engine)] == ["refusal", "error", "error"]


async def test_one_question_at_a_time(
    app: FastAPI, client: AsyncClient, world: SimpleNamespace
) -> None:
    app.state.ask_guard._active.add(world.a)
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (409, "ask_in_progress")


async def test_read_timeout_is_503_with_fixed_message(
    real_gateway: Gateway, caplog: pytest.LogCaptureFixture
) -> None:
    """Cùng status với ai_unavailable, nhưng người dùng biết model quá chậm chứ không phải tắt."""
    real_gateway.replies = [httpx.ReadTimeout("SECRET-TIMEOUT-TEXT")]
    with caplog.at_level("DEBUG"):
        r = await ask(real_gateway.client)
    assert (r.status_code, r.json()["code"]) == (503, "ai_timeout")
    assert r.json()["detail"] == TIMEOUT_MESSAGE
    assert "SECRET" not in r.text and "SECRET" not in caplog.text


@pytest.fixture
async def no_llm_client(
    api: AsyncEngine, ml: FakeMl, world: SimpleNamespace
) -> AsyncIterator[AsyncClient]:
    """Như client nhưng app không có client AI giả: chỉ còn nhà cung cấp lưu trong DB."""
    app = create_app(api, ml=ml, web_dist=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        yield c


async def test_env_key_ignored_without_provider(
    api: AsyncEngine, ml: FakeMl, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đặt biến trước create_app: chặn cả code đọc môi trường lúc dựng app, không chỉ lúc gọi."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.example")
    app = create_app(api, ml=ml, web_dist=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        r = await ask(c)
        assert (r.status_code, r.json()["code"]) == (503, "ai_not_configured")
        assert (await c.get("/api/v1/ai/settings")).json()["configured"] is False
    assert ml.embedded == []  # dừng trước bước tìm kiếm


def test_app_state_has_no_llm(api: AsyncEngine, ml: FakeMl, llm: FakeLlm) -> None:
    """Client giả chỉ đi vào LlmResolver, không có đường thứ hai (spec 28 mục 4)."""
    app = create_app(api, ml=ml, llm=llm, web_dist=None)
    assert not hasattr(app.state, "llm")
    assert app.state.llm_resolver.default is llm


async def test_user_with_provider_but_no_model_gets_model_missing(
    client: AsyncClient, owner_engine: Engine, world: SimpleNamespace, llm: FakeLlm
) -> None:
    with owner_engine.begin() as conn:  # người dùng cũ: chỉ có cấp, migration đã xóa dòng đó
        conn.execute(text("delete from user_ai_settings where user_id = :u"), {"u": world.a})
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (503, "ai_model_missing")
    assert llm.requests == []


async def test_ask_log_has_no_question(
    real_gateway: Gateway, caplog: pytest.LogCaptureFixture
) -> None:
    """Đi đủ hai lượt gọi qua client thật: text mail, câu hỏi, key, URL đầy đủ không vào log."""
    real_gateway.replies = [chat_reply(plan()), chat_reply(ANSWER)]
    with caplog.at_level("DEBUG"):
        r = await ask(real_gateway.client, "田中さんの見積はいくらでしたか?")
    assert r.status_code == 200, r.text
    assert len(real_gateway.requests) == 2
    answer_request = real_gateway.requests[1].content.decode()
    assert "CT-2026-0042" in answer_request  # text mail đã thật sự đi qua bước trả lời
    assert real_gateway.requests[0].headers["authorization"] == f"Bearer {GATEWAY_KEY}"
    for secret in (
        "田中",
        "見積",
        "サンプルA",
        "1,250,000",
        "CT-2026-0042",
        GATEWAY_KEY,
        GATEWAY_URL,
        "secret-path",
    ):
        assert secret not in caplog.text, secret
    assert "ask user=" in caplog.text and "ai user=" in caplog.text


async def test_ask_requires_csrf_header(client: AsyncClient, llm: FakeLlm) -> None:
    r = await client.post("/api/v1/ask", json={"question": "báo giá"})
    assert r.status_code == 403
    assert llm.requests == []


ASK_MODELS = {"question_analysis": "m-analysis", "answer": "m-answer"}


async def test_ask_ignores_missing_translate_model(client: AsyncClient, llm: FakeLlm) -> None:
    await client.put(
        "/api/v1/ai/settings", json={"models": {**ASK_MODELS, "translate": None}}, headers=CSRF
    )
    llm.responses = [plan(), ANSWER]
    r = await ask(client)
    assert r.status_code == 200, r.text
    assert [q["model"] for q in llm.requests] == ["m-analysis", "m-answer"]


async def test_translate_requires_its_own_model(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    await client.put(
        "/api/v1/ai/settings", json={"models": {**ASK_MODELS, "translate": None}}, headers=CSRF
    )
    r = await client.post(f"/api/v1/messages/{world.est}/translate", json={}, headers=CSRF)
    assert (r.status_code, r.json()["code"]) == (503, "ai_model_missing")
    assert llm.requests == []


async def test_translate_needs_only_its_own_model(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    await client.put(
        "/api/v1/ai/settings",
        json={"models": {"translate": "m-t", "question_analysis": None, "answer": None}},
        headers=CSRF,
    )
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await client.post(f"/api/v1/messages/{world.est}/translate", json={}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert llm.requests[0]["model"] == "m-t"
    # /ask vẫn cần model của hai tác vụ của nó.
    assert (await ask(client)).json()["code"] == "ai_model_missing"


async def test_long_subject_cut_at_limit(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    subject = "見積" + "件" * 400
    with owner_engine.begin() as conn:
        put_mail(
            conn, world.a, "long@a.example.jp", [world.fa], subject=subject, body="本文", day=4
        )
    await process_user(worker, world.a)
    llm.responses = [plan(keywords=["見積"]), ANSWER]
    r = await ask(client)
    assert r.status_code == 200, r.text
    line = next(ln for ln in llm.requests[1]["user"].splitlines() if "件" * 100 in ln)
    assert line == "Subject: " + subject[:300]


LONG_DOMAIN = ".".join(f"dept-{i:04d}-long" for i in range(7)) + ".sample-b.example.jp"


async def test_long_address_cut_at_limit(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    sender, recipient = f"tanaka.taro@{LONG_DOMAIN}", f"sato.hanako+{'x' * 90}@sample-b.example.jp"
    limit = ask_module.MAX_NAME_CHARS
    assert len(sender) > limit and len(recipient) > limit
    with owner_engine.begin() as conn:
        put_mail(
            conn,
            world.a,
            "addr@a.example.jp",
            [world.fa],
            subject="見積",
            body="本文",
            frm=sender,
            to=(recipient,),
            day=4,
        )
    await process_user(worker, world.a)
    llm.responses = [plan(keywords=["見積"]), ANSWER]
    assert (await ask(client)).status_code == 200
    sent = llm.requests[1]["user"]
    assert f"From: {sender[:limit]}\n" in sent and f"To: {recipient[:limit]}\n" in sent
    assert sender not in sent and recipient not in sent


async def test_long_name_cut_at_limit(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    with owner_engine.begin() as conn:
        put_mail(
            conn,
            world.a,
            "name@a.example.jp",
            [world.fa],
            subject="見積",
            body="本文",
            from_name="ながいなまえ" * 100,
            day=4,
        )
    await process_user(worker, world.a)
    llm.responses = [plan(keywords=["見積"]), ANSWER]
    assert (await ask(client)).status_code == 200
    line = next(ln for ln in llm.requests[1]["user"].splitlines() if "ながいなまえ" in ln)
    assert len(line.removeprefix("From: ")) <= ask_module.MAX_NAME_CHARS * 2 + 3


async def test_only_the_retrieved_chunk_is_sent(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SOURCES đọc đúng đoạn [start, end) của body_clean, không đệm thêm."""
    with owner_engine.begin() as conn:
        m = put_mail(
            conn,
            world.a,
            "chunk@a.example.jp",
            [world.fa],
            subject="件名",
            body="前半の文章。中盤の文章。後半の文章。",
            day=5,
        )
    await process_user(worker, world.a)
    with owner_engine.connect() as conn:
        body = conn.scalar(text("select body_clean from messages where id = :m"), {"m": m})

    async def retrieve(*_: object) -> list[tuple[int, int, int]]:
        return [(m, body.index("中盤"), body.index("後半"))]

    monkeypatch.setattr(ask_module, "_retrieve", retrieve)
    llm.responses = [plan(), ANSWER]
    assert (await ask(client)).status_code == 200
    chunk = llm.requests[-1]["user"].split("\n\n", 1)[1].split("\n</source>")[0]
    assert chunk == "中盤の文章。"
