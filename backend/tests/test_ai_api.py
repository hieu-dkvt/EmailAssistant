"""Hỏi đáp AI (spec 21) và cài đặt model (spec 22) với AI giả và ml-service giả.

Kiểm tra quan trọng nhất (NDA): request gửi AI không chứa giá trị gốc nào đã phải che, và
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
from app.ai import ask as ask_module
from app.ai import people
from app.ai.client import LlmError, LlmResponse, LlmUsage
from app.ai.prompts import AnswerOut, QuestionPlan, TranslationOut
from app.db.session import create_api_engine
from app.main import create_app
from app.processing import process_user
from tests.dbutil import add_source, set_ai_models, url_for_test_db
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
        for u in (a, b):
            set_ai_models(conn, u)
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
    # "Nội dung đã gửi AI" đúng là text trong request.
    assert [s["content"] for s in data["sent"]] == [req["user"] for req in llm.requests]
    assert data["mask_counts"]["PERSON"] >= 2 and data["mask_counts"]["MONEY"] == 1
    assert [c["task"] for c in data["calls"]] == ["question_analysis", "answer"]
    # Tên model là tên người dùng nhập; không còn tham số fallbacks.
    assert [q["model"] for q in llm.requests] == ["test-model", "test-model"]
    assert all("fallbacks" not in q for q in llm.requests)
    assert ai_calls(owner_engine) == [
        (world.a, "question_analysis", "test-model", "ok", 100),
        (world.a, "answer", "test-model", "ok", 100),
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
    no_llm_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.setenv(name, "x")
    r = await ask(no_llm_client)
    assert (r.status_code, r.json()["code"]) == (503, "ai_not_configured")
    assert (await no_llm_client.get("/api/v1/ai/settings")).json()["configured"] is False


async def test_user_with_provider_but_no_model_gets_model_missing(
    client: AsyncClient, owner_engine: Engine, world: SimpleNamespace, llm: FakeLlm
) -> None:
    with owner_engine.begin() as conn:  # người dùng cũ: chỉ có cấp, migration đã xóa dòng đó
        conn.execute(text("delete from user_ai_settings where user_id = :u"), {"u": world.a})
    r = await ask(client)
    assert (r.status_code, r.json()["code"]) == (503, "ai_model_missing")
    assert llm.requests == []


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


async def test_long_subject_masked_before_cut(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Vị trí 300 rơi vào giữa số điện thoại: cắt trước khi che thì mảnh số không khớp mẫu che."""
    subject = "見積" + "件" * 290 + "(090-1234-5678) 以上"
    with owner_engine.begin() as conn:
        put_mail(
            conn, world.a, "long@a.example.jp", [world.fa], subject=subject, body="本文", day=4
        )
    await process_user(worker, world.a)
    llm.responses = [plan(keywords=["見積"]), ANSWER]
    r = await ask(client)
    assert r.status_code == 200, r.text
    line = next(ln for ln in llm.requests[1]["user"].splitlines() if "件" * 290 in ln)
    assert "090" not in line and "1234" not in line, line
    assert len(line.removeprefix("Subject: ")) <= 300


LONG_DOMAIN = ".".join(f"dept-{i:04d}-long" for i in range(7)) + ".sample-b.example.jp"


async def test_long_address_masked_before_cut(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Địa chỉ trên 100 ký tự: cắt trước khi che thì phần đầu không khớp mẫu email, lọt ra."""
    sender, recipient = f"tanaka.taro@{LONG_DOMAIN}", f"sato.hanako+{'x' * 90}@sample-b.example.jp"
    assert len(sender) > ask_module.MAX_NAME_CHARS and len(recipient) > ask_module.MAX_NAME_CHARS
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
    assert f"Subject: {'見積'}" in sent  # mail đúng là một nguồn đã gửi
    for piece in ("tanaka.taro", "sato.hanako", "xxxx", "dept-", "-000", "sample-b"):
        assert piece not in sent, piece


async def test_long_name_cut_after_mask(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Giá trị không che được vẫn bị giới hạn độ dài sau khi che."""
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


async def test_subject_sent_to_ner_capped_at_ingest_limit(
    client: AsyncClient,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """20 nguồn không được đưa 800.000 ký tự tới NER: subject che tối đa 10.000 ký tự."""
    subject = "見積" + "件" * 11_998
    with owner_engine.begin() as conn:
        put_mail(conn, world.a, "cap@a.example.jp", [world.fa], subject=subject, body="本文", day=4)
    await process_user(worker, world.a)
    llm.responses = [plan(keywords=["見積"]), ANSWER]
    assert (await ask(client)).status_code == 200
    assert any("件" * 9_000 in t for t in ml.ner_inputs)  # mail dài đúng là nguồn đã che
    assert max(len(t) for t in ml.ner_inputs) <= 10_000


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


# --- che theo cửa sổ rộng hơn đoạn (bước gửi đi của /ask) ---

# Có 、。: chuỗi liền không ngắt thì quy tắc URL dài coi cả đoạn là một cụm URL.
FILLER = ("あ" * 9 + "。") * 40
LABELLED = [
    pytest.param("契約番号 ", "CT-7731-0042", "\n", id="contract-ja"),
    pytest.param("Contract No. ", "CT-7731-0042", "\n", id="contract-en"),
    pytest.param("金額 ", "1,200,000", "（税別）\n", id="money-ja"),
    pytest.param("Chi phí: ", "35.000.000", "\n", id="money-vi"),
    pytest.param("Address: ", "12 Example Street", "\n", id="address"),
    pytest.param("連絡先 ", "suzuki.ichiro@sample-b.example.jp", " まで\n", id="email"),
    pytest.param(
        "資料 ", "https://portal.sample-b.example.jp/alpha/spec.html", " 参照\n", id="url"
    ),
]


def sent_bodies(user: str) -> str:
    """Phần đoạn mail của mọi nguồn đã gửi, không có header (ngày trong header có chữ số)."""
    sources = user.split("<sources>", 1)[1].split('<source id="')[1:]
    return "\n".join(s.split("\n\n", 1)[1].split("</source>")[0].strip() for s in sources)


def fragments(value: str) -> list[str]:
    """Giá trị và mọi mảnh đầu, mảnh đuôi từ 4 ký tự: không mảnh nào được ra ngoài."""
    heads = [value[:i] for i in range(4, len(value) + 1)]
    return heads + [value[i:] for i in range(len(value) - 3)]


async def add_body_mail(
    owner_engine: Engine, worker: AsyncEngine, world: SimpleNamespace, body: str
) -> tuple[int, str]:
    with owner_engine.begin() as conn:
        m = put_mail(
            conn, world.a, "cut@a.example.jp", [world.fa], subject="件名", body=body, day=5
        )
    await process_user(worker, world.a)
    with owner_engine.connect() as conn:
        clean = conn.scalar(text("select body_clean from messages where id = :m"), {"m": m})
    return m, clean


def only_chunk(monkeypatch: pytest.MonkeyPatch, key: tuple[int, int, int]) -> None:
    """Chỉ gửi một đoạn: nhiều đoạn trong một lần hỏi thì từ điển che giúp nhau, test vô nghĩa."""

    async def retrieve(*_: object) -> list[tuple[int, int, int]]:
        return [key]

    monkeypatch.setattr(ask_module, "_retrieve", retrieve)


async def sent_chunk(client: AsyncClient, llm: FakeLlm) -> str:
    llm.requests.clear()
    llm.responses = [plan(), ANSWER]
    r = await ask(client)
    assert r.status_code == 200, r.text
    return sent_bodies(llm.requests[-1]["user"])


@pytest.mark.parametrize(("label", "value", "after"), LABELLED)
async def test_cut_anywhere_never_sends_value(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    value: str,
    after: str,
) -> None:
    """Điểm cắt đi qua từng ký tự của nhãn + giá trị, xét cả đoạn trước lẫn đoạn sau điểm cắt."""
    m, body = await add_body_mail(
        owner_engine, worker, world, f"{FILLER}{label}{value}{after}{FILLER}"
    )
    start = body.index(label)
    for cut in range(start, start + len(label) + len(value) + 1):
        for key in ((m, 0, cut), (m, cut, len(body))):
            only_chunk(monkeypatch, key)
            sent = await sent_chunk(client, llm)
            for piece in fragments(value):
                assert piece not in sent, (cut, key[1], piece)


async def test_url_longer_than_window_sends_no_tail(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đầu URL nằm ngoài cửa sổ nên không mẫu nào khớp: phần đuôi trong đoạn cũng không được gửi."""
    url = "https://portal.sample-b.example.jp/" + "alpha-beta/" * 60 + "token=SECRETTOKEN"
    m, body = await add_body_mail(owner_engine, worker, world, f"{FILLER}{url} 以上{FILLER}")
    cut = body.index("token=") - 11 * 3
    only_chunk(monkeypatch, (m, cut, len(body)))
    sent = await sent_chunk(client, llm)
    assert "alpha-beta" not in sent and "SECRETTOKEN" not in sent
    assert "以上" in sent  # phần sau URL vẫn được gửi


async def test_unencoded_japanese_url_longer_than_window_sends_no_tail(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URL giả chưa mã hóa, có chữ Nhật giữa đường dẫn, dài hơn 200 ký tự: điểm cắt nào cũng không
    gửi phần đuôi. Quy tắc cũ dừng ở ký tự không ASCII đầu tiên nên để lọt phần sau chữ Nhật."""
    url = (
        "https://portal.sample-b.example.jp/"
        + "alpha-beta/" * 25
        + "資料/"
        + "gamma-delta/" * 25
        + "token=SECRETTOKEN"
    )
    assert len(url) > 200
    m, body = await add_body_mail(owner_engine, worker, world, f"{FILLER}{url} 以上{FILLER}")
    head = body.index("https")
    for cut in range(head + 1, body.index(" 以上"), 7):
        only_chunk(monkeypatch, (m, cut, len(body)))
        sent = await sent_chunk(client, llm)
        assert "gamma-delta" not in sent and "SECRETTOKEN" not in sent, cut
        assert "以上" in sent


async def test_real_chunks_contract_after_label(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Không can thiệp bước tìm: đoạn thứ hai của split_chunks bắt đầu ngay sau nhãn."""
    label = "契約番号 "
    start = embedding.CHUNK_CHARS - embedding.CHUNK_OVERLAP - len(label)
    body = "あ" * start + label + "CT-7731-0042 の件" + "あ" * 300
    with owner_engine.begin() as conn:
        put_mail(conn, world.a, "chunks@a.example.jp", [world.fa], subject="見積", body=body, day=5)
    await process_user(worker, world.a)
    await embedding.embed_user(worker, FakeMl(), world.a)
    assert any(c.start == start + len(label) for c in embedding.split_chunks(body))
    sent = await sent_chunk(client, llm)
    assert "] の件" in sent  # đoạn thứ hai đã gửi, số hợp đồng thành nhãn
    for piece in fragments("CT-7731-0042"):
        assert piece not in sent, piece


async def test_window_labels_only_what_is_sent(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Giá trị chỉ nằm trong phần đệm của cửa sổ thì không có nhãn: mask_counts đúng phần đã gửi."""
    body = f"連絡先 03-0000-0009 {'あ' * 150}本文の前半{'あ' * 150}本文の後半"
    m, body = await add_body_mail(owner_engine, worker, world, body)
    only_chunk(monkeypatch, (m, body.index("本文の前半"), body.index("本文の後半")))
    llm.requests.clear()
    llm.responses = [plan(), ANSWER]
    data = (await ask(client)).json()
    sent = sent_bodies(llm.requests[-1]["user"])
    assert sent.startswith("本文の前半") and sent.endswith("あ")
    assert "PHONE" not in data["mask_counts"]
