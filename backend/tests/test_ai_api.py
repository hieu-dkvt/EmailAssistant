"""Hỏi đáp AI (spec 21) và cài đặt model (spec 22) với Claude giả và ml-service giả.

Kiểm tra quan trọng nhất (NDA): request gửi Claude không chứa giá trị gốc nào đã phải che, và
không chứa mail của người khác.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import embedding
from app.ai import people
from app.ai.client import LlmError, LlmResponse, LlmUsage
from app.ai.prompts import AnswerOut, QuestionPlan
from app.db.session import create_api_engine
from app.main import create_app
from app.processing import process_user
from tests.dbutil import add_source, url_for_test_db
from tests.mlfake import FakeLlm, FakeMl
from tests.test_web_api import put_mail
from tests.webutil import CSRF, add_web_user, login

TANAKA = "tanaka@client-a.example.jp"
SECRETS = (
    "田中",
    "健二",
    "tanaka@",
    "client-a.example.jp",
    "サンプルA",
    "1,250,000",
    "03-0000-0001",
    "CT-2026-0042",
    "高橋",
)


@pytest.fixture(autouse=True)
def fresh_people_cache() -> None:
    people.clear_cache()


@pytest.fixture
async def world(owner_engine: Engine, worker: AsyncEngine, clean: None) -> SimpleNamespace:
    a = add_web_user(owner_engine, "a@example.com")
    b = add_web_user(owner_engine, "b@example.com")
    with owner_engine.begin() as conn:
        fa = add_source(conn, a, "ImapMail/imap.example.com/INBOX")
        fb = add_source(conn, b, "ImapMail/imap.example.com/INBOX")
        est = put_mail(
            conn,
            a,
            "est@a.example.jp",
            [fa],
            subject="【サンプルA】お見積りの送付",
            # Tên công ty chỉ có trong chữ ký (bị cắt khỏi body_clean) và dạng viết tắt ở subject.
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
    return FakeMl(ner_names=("高橋",))


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
    answer="[PERSON_1]様([ORG_1])が[MONEY_1]でお見積りを送りました [1]. Liên hệ [PHONE_1].",
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


async def test_ask_masks_everything_sent_and_unmasks_answer(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [plan(), ANSWER]
    r = await ask(client, "田中さんの見積はいくらでしたか?")
    assert r.status_code == 200, r.text
    data = r.json()
    sent = llm.sent_text()
    for secret in SECRETS:
        assert secret not in sent, secret
    assert "SECRET-OF-B" not in sent
    user_parts = "\n".join(req["user"] for req in llm.requests)
    for label in ("[PERSON_1]", "[MONEY_1]", "[CONTRACT_1]", "[PHONE_1]", "[ORG_1]"):
        assert label in user_parts, label
    # Câu trả lời đã thay nhãn về giá trị thật; nguồn 7 không tồn tại bị bỏ.
    assert "田中" in data["answer"] and "1,250,000円" in data["answer"]
    assert "サンプルA" in data["answer"] and "03-0000-0001" in data["answer"]
    assert [s["message_id"] for s in data["sources"] if s["cited"]] == [world.est]
    assert world.b_est not in [s["message_id"] for s in data["sources"]]
    # "Nội dung đã gửi Claude" đúng là text trong request.
    assert [s["content"] for s in data["sent"]] == [req["user"] for req in llm.requests]
    assert data["mask_counts"]["PERSON"] >= 2 and data["mask_counts"]["MONEY"] == 1
    assert [c["task"] for c in data["calls"]] == ["question_analysis", "answer"]
    # Model mặc định: Haiku phân tích (không fallback), Sonnet trả lời (có fallback).
    assert [(q["model"], q["fallbacks"]) for q in llm.requests] == [
        ("claude-haiku-5-5", False),
        ("claude-sonnet-5-5", True),
    ]
    assert ai_calls(owner_engine) == [
        (world.a, "question_analysis", "claude-haiku-5-5", "ok", 100),
        (world.a, "answer", "claude-sonnet-5-5", "ok", 100),
    ]


async def test_plan_values_unmasked_before_local_search(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [plan(sender="[PERSON_1]", sent_after="2026-09-01"), ANSWER]
    r = await ask(client, "田中さんは何を送りましたか")
    data = r.json()
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
    r = await client.get("/api/v1/ai/settings")
    data = r.json()
    assert {t["task"]: t["tier"] for t in data["tasks"]} == {
        "question_analysis": "haiku",
        "answer": "sonnet",
    }
    assert [t["model"] for t in data["tiers"]] == [
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
    ]
    assert len(data["masking"]) == 4
    r = await client.put(
        "/api/v1/ai/settings",
        json={"tiers": {"question_analysis": "sonnet", "answer": "opus"}},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert {t["task"]: t["tier"] for t in r.json()["tasks"]} == {
        "question_analysis": "sonnet",
        "answer": "opus",
    }
    llm.responses = [plan(), ANSWER]
    await ask(client)
    assert [q["model"] for q in llm.requests] == ["claude-sonnet-5-5", "claude-opus-5-5"]
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
        tiers = {
            t["task"]: t["tier"] for t in (await cb.get("/api/v1/ai/settings")).json()["tasks"]
        }
    assert tiers == {"question_analysis": "haiku", "answer": "sonnet"}


@pytest.mark.parametrize(
    "body",
    [{"tiers": {"answer": "gpt"}}, {"tiers": {"unknown_task": "haiku"}}, {"tiers": "x"}],
)
async def test_settings_validation(client: AsyncClient, body: dict[str, object]) -> None:
    r = await client.put("/api/v1/ai/settings", json=body, headers=CSRF)
    assert r.status_code == 422


async def test_settings_requires_csrf_and_login(app: FastAPI, client: AsyncClient) -> None:
    r = await client.put("/api/v1/ai/settings", json={"tiers": {}})
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
                "values (:b, 'answer', 'claude-opus-5-5', 'ok', 999, 999)"
            ),
            {"b": world.b},
        )
        conn.execute(
            text(
                "insert into ai_calls (user_id, task, model, status, input_tokens, created_at) "
                "values (:a, 'answer', 'claude-opus-5-5', 'ok', 999, now() - interval '40 days')"
            ),
            {"a": world.a},
        )
    data = (await client.get("/api/v1/ai/usage")).json()
    assert [(r["model"], r["calls"], r["input_tokens"]) for r in data["rows"]] == [
        ("claude-haiku-5-5", 1, 100),
        ("claude-sonnet-5-5", 1, 100),
    ]
    expected = (100 * 0.10 + 20 * 0.50 + 100 * 2.0 + 20 * 10.0) / 1_000_000
    assert data["total_estimated_usd"] == pytest.approx(expected)
    assert data["total_calls"] == 2


async def test_ner_failure_sends_nothing(client: AsyncClient, ml: FakeMl, llm: FakeLlm) -> None:
    ml.ner_down = True
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (503, "ml_unavailable")
    assert llm.requests == []


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
    llm.responses = [LlmError("ai_unavailable", 503, "Không kết nối được Claude")]
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


async def test_not_configured(
    api: AsyncEngine, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    app = create_app(api, ml=FakeMl(), web_dist=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        r = await ask(c)
        assert (r.status_code, r.json()["code"]) == (503, "ai_not_configured")
        assert (await c.get("/api/v1/ai/settings")).json()["configured"] is False


async def test_ask_log_has_no_question(
    client: AsyncClient, llm: FakeLlm, caplog: pytest.LogCaptureFixture
) -> None:
    llm.responses = [plan(), ANSWER]
    with caplog.at_level("INFO"):
        await ask(client, "田中さんの見積はいくらでしたか?")
    assert "田中" not in caplog.text and "見積" not in caplog.text
    assert "ask user=" in caplog.text and "ai user=" in caplog.text


async def test_ask_requires_csrf_header(client: AsyncClient, llm: FakeLlm) -> None:
    r = await client.post("/api/v1/ask", json={"question": "báo giá"})
    assert r.status_code == 403
    assert llm.requests == []


async def test_dictionary_only_from_own_mail(
    api: AsyncEngine, owner_engine: Engine, world: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        fb = conn.scalar(text("select id from mail_sources where user_id = :u"), {"u": world.b})
        put_mail(
            conn, world.b, "b-only@b.example.jp", [fb], subject="x", body="y",
            frm="secret-person@b-only.example.jp", from_name="秘密 太郎",
        )  # fmt: skip
    from app.db.session import user_session

    async with user_session(api, world.a) as s:
        ents = await people.correspondents(s, world.a)
    surfaces = {x for e in ents for x in e.surfaces}
    assert "田中 健二" in surfaces
    assert not any("秘密" in x or "b-only" in x for x in surfaces)
