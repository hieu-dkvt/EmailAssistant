# ruff: noqa: F811, F401
"""Hỏi đáp gửi text gốc, không che (spec 28 mục 5): cut_text và luồng /ask."""

from types import SimpleNamespace

from httpx import AsyncClient
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app import embedding
from app.ai.prompts import ANALYSIS_SYSTEM, ANSWER_SYSTEM, AnswerOut
from app.ai.text import cut_text
from app.processing import process_user
from tests.mlfake import FakeLlm, FakeMl
from tests.test_ai_api import (
    ANSWER,
    api,
    app,
    ask,
    client,
    llm,
    ml,
    plan,
    world,
)
from tests.test_web_api import put_mail


def test_cut_text_not_needed() -> None:
    assert cut_text("abc", 5) == ("abc", False)


def test_cut_text_at_paragraph() -> None:
    assert cut_text("aaaaaa\n\nbbbb\ncc", 12) == ("aaaaaa", True)


def test_cut_text_at_line() -> None:
    assert cut_text("aaaaaa\nbbbb", 8) == ("aaaaaa", True)


def test_cut_text_hard_when_no_break() -> None:
    assert cut_text("a" * 20, 8) == ("a" * 8, True)


def test_cut_text_ignores_break_in_first_half() -> None:
    assert cut_text("a\n" + "b" * 20, 10) == ("a\n" + "b" * 8, True)


async def test_sends_original_text(
    client: AsyncClient, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [plan(), ANSWER]
    r = await ask(client, "田中さんの見積はいくらでしたか?")
    assert r.status_code == 200, r.text
    assert "田中 健二" in llm.sent_text() and "1,250,000円" in llm.sent_text()
    assert "田中さんの見積" in llm.requests[0]["user"]
    assert "[PERSON_" not in llm.requests[-1]["user"]
    assert "SECRET-OF-B" not in llm.sent_text()
    data = r.json()
    assert "mask_counts" not in data
    assert [s["content"] for s in data["sent"]] == [req["user"] for req in llm.requests]


def test_prompts_do_not_mention_placeholders() -> None:
    assert "placeholder" not in (ANALYSIS_SYSTEM + ANSWER_SYSTEM).lower()
    assert "[PERSON_1]" not in ANALYSIS_SYSTEM + ANSWER_SYSTEM


async def test_label_like_text_kept_verbatim(
    client: AsyncClient,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Mail có sẵn chuỗi "[PERSON_1]", AI trả "[PERSON_1]": không ai thay thế gì."""
    with owner_engine.begin() as conn:
        put_mail(
            conn,
            world.a,
            "label@a.example.jp",
            [world.fa],
            subject="見積 [ORG_9]",
            body="[PERSON_1]様 見積の件です [MONEY_1]",
            day=4,
        )
    await process_user(worker, world.a)
    await embedding.embed_user(worker, FakeMl(), world.a)
    answer = AnswerOut(
        answer="[PERSON_1] が [MONEY_1] を送付 [1]", cited_sources=[1], insufficient=False
    )
    llm.responses = [plan(keywords=["見積"]), answer]
    r = await ask(client)
    assert r.status_code == 200, r.text
    sent = llm.requests[-1]["user"]
    assert "[PERSON_1]様 見積の件です [MONEY_1]" in sent and "見積 [ORG_9]" in sent
    assert r.json()["answer"] == "[PERSON_1] が [MONEY_1] を送付 [1]"


async def test_plan_values_used_as_is(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
) -> None:
    """sender, search_queries, keywords dùng nguyên giá trị AI trả, không qua thay ngược."""
    llm.responses = [
        plan(sender="田中", search_queries=["[PERSON_1] 見積"], keywords=["見積", "[ORG_1]"]),
        ANSWER,
    ]
    data = (await ask(client)).json()
    assert data["plan"]["sender"] == "田中"
    assert data["plan"]["search_queries"] == ["[PERSON_1] 見積"]
    assert data["plan"]["keywords"] == ["見積", "[ORG_1]"]
    assert [s["message_id"] for s in data["sources"]] == [world.est]
