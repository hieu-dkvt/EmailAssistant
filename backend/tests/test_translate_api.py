# ruff: noqa: F811
"""API dịch tay và bản dịch theo thread (spec 26 mục 6, 10), AI giả."""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.client import TIMEOUT_MESSAGE, LlmError
from app.ai.prompts import TranslationOut
from app.main import create_app
from app.processing import process_user
from tests.dbutil import add_auto_run, add_glossary, add_project, add_source, add_translation
from tests.mlfake import FakeLlm, FakeMl
from tests.test_ai_api import (  # noqa: F401  (fixture dùng chung)
    GATEWAY_KEY,
    GATEWAY_URL,
    Gateway,
    api,
    chat_reply,
    client,
    llm,
    ml,
    real_gateway,
    world,
)
from tests.test_ai_api import app as app  # noqa: F401
from tests.test_web_api import put_mail
from tests.webutil import CSRF, login

GOOD = TranslationOut(subject="サンプルA báo giá", body="Số tiền 1,250,000円.", notes=[])


async def translate(client: AsyncClient, mid: int, **body: Any):
    return await client.post(f"/api/v1/messages/{mid}/translate", json=body, headers=CSRF)


def translation_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.scalar(text("select count(*) from message_translations")) or 0


async def test_translate_saves_and_returns(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [GOOD]
    r = await translate(client, world.est)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["message_id"] == world.est
    assert "サンプルA" in data["translated_subject"]
    assert "1,250,000円" in data["translated_body"]
    assert data["stale"] is False and data["model"] == "test-model"
    assert data["notes"] == []
    assert translation_count(owner_engine) == 1


async def test_saved_translation_no_ai_call(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [GOOD]
    first = await translate(client, world.est)
    r = await translate(client, world.est)
    assert r.status_code == 200 and r.json() == first.json()
    assert len(llm.requests) == 1


async def test_force_calls_again(client: AsyncClient, llm: FakeLlm, world: SimpleNamespace) -> None:
    llm.responses = [GOOD, TranslationOut(subject="s2", body="b2", notes=[])]
    await translate(client, world.est)
    r = await translate(client, world.est, force=True)
    assert r.json()["translated_subject"] == "s2"
    assert len(llm.requests) == 2


async def test_other_users_mail_404_no_ai_call(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    r = await translate(client, world.b_est, force=True)
    assert (r.status_code, r.json()["code"]) == (404, "message_not_found")
    assert llm.requests == []


async def test_not_cleaned_409(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        m = put_mail(conn, world.a, "raw@a.example.jp", [world.fa], subject="s", body="b")
    r = await translate(client, m)
    assert (r.status_code, r.json()["code"]) == (409, "mail_not_ready")
    assert llm.requests == []


async def test_empty_400(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    with owner_engine.begin() as conn:
        m = put_mail(conn, world.a, "e@a.example.jp", [world.fa], subject="", body="")
    await process_user(worker, world.a)
    r = await translate(client, m)
    assert (r.status_code, r.json()["code"]) == (400, "nothing_to_translate")
    assert llm.requests == []


async def test_in_progress_409(
    app: FastAPI, client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    app.state.translate_guard._active.add(world.a)
    r = await translate(client, world.est)
    assert (r.status_code, r.json()["code"]) == (409, "translate_in_progress")
    assert llm.requests == []


async def test_guard_separate_from_ask(
    app: FastAPI, client: AsyncClient, llm: FakeLlm, world: SimpleNamespace
) -> None:
    app.state.ask_guard._active.add(world.a)
    llm.responses = [GOOD]
    assert (await translate(client, world.est)).status_code == 200


async def test_ai_error_saves_nothing(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [LlmError("ai_unavailable", 503, "Không kết nối được AI")]
    r = await translate(client, world.est)
    assert (r.status_code, r.json()["code"]) == (503, "ai_unavailable")
    assert translation_count(owner_engine) == 0
    llm.responses = [TranslationOut(subject="x", body=" ", notes=[])]
    r = await translate(client, world.est)
    assert (r.status_code, r.json()["code"]) == (502, "ai_bad_output")
    assert translation_count(owner_engine) == 0


async def test_translate_route_works_when_ml_down(
    client: AsyncClient, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """Dịch không còn gọi ml-service (spec 28)."""
    ml.down = True
    llm.responses = [GOOD]
    assert (await translate(client, world.est)).status_code == 200
    assert len(llm.requests) == 1
    assert ml.embedded == []


async def test_unexpected_error_hides_message(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm.responses = [RuntimeError("mật khẩu 田中 trong mail")]
    with caplog.at_level("INFO"):
        r = await translate(client, world.est)
    assert (r.status_code, r.json()["code"]) == (500, "translate_failed")
    assert "田中" not in r.text and "田中" not in caplog.text
    assert "translate failed" in caplog.text


async def test_requires_csrf(client: AsyncClient, llm: FakeLlm, world: SimpleNamespace) -> None:
    r = await client.post(f"/api/v1/messages/{world.est}/translate", json={})
    assert r.status_code == 403
    assert llm.requests == []


async def test_not_configured_503(
    api: AsyncEngine, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # không còn được đọc
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
    app = create_app(api, ml=FakeMl(), web_dist=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        r = await translate(c, world.est)
    assert (r.status_code, r.json()["code"]) == (503, "ai_not_configured")


async def test_saved_translation_works_without_ai_configured(
    api: AsyncEngine,
    world: SimpleNamespace,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # không còn được đọc
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
    with owner_engine.begin() as conn:
        add_translation(conn, world.a, world.est)
    app = create_app(api, ml=FakeMl(), web_dist=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        r = await translate(c, world.est)
    assert r.status_code == 200 and r.json()["stale"] is True


async def test_stale_after_glossary_change(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "お見積り", "báo giá")
    llm.responses = [TranslationOut(subject="s", body="báo giá", notes=[])]
    assert (await translate(client, world.est)).json()["stale"] is False
    assert (await translate(client, world.est)).json()["stale"] is False
    with owner_engine.begin() as conn:
        conn.execute(text("update glossary_terms set translation = 'dự toán'"))
    assert (await translate(client, world.est)).json()["stale"] is True
    with owner_engine.begin() as conn:
        conn.execute(text("delete from glossary_terms"))
    assert (await translate(client, world.est)).json()["stale"] is True


async def test_conflict_translation_not_stale_until_changed(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        for name, translation in (("P1", "báo giá"), ("P2", "dự toán")):
            p = add_project(conn, world.a, name)
            add_glossary(conn, world.a, "お見積り", translation, project_id=p)
            conn.execute(
                text(
                    "insert into message_projects (user_id, message_id, project_id) "
                    "values (:u, :m, :p)"
                ),
                {"u": world.a, "m": world.est, "p": p},
            )
    llm.responses = [TranslationOut(subject="s", body="dự toán", notes=[])]
    r = await translate(client, world.est)
    assert r.json()["stale"] is False and "glossary_conflict" in r.json()["warnings"]
    with owner_engine.begin() as conn:
        conn.execute(text("delete from glossary_terms where translation = 'dự toán'"))
    assert (await translate(client, world.est)).json()["stale"] is True


async def test_term_of_other_project_does_not_hide_stale(
    client: AsyncClient, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    """Mail rời dự án: mục dự án không còn áp dụng, dù mục cùng thuật ngữ vẫn còn trong bảng."""
    with owner_engine.begin() as conn:
        p = add_project(conn, world.a, "P1")
        add_glossary(conn, world.a, "お見積り", "báo giá", project_id=p)
        conn.execute(
            text(
                "insert into message_projects (user_id, message_id, project_id) values (:u, :m, :p)"
            ),
            {"u": world.a, "m": world.est, "p": p},
        )
    llm.responses = [TranslationOut(subject="s", body="báo giá", notes=[])]
    assert (await translate(client, world.est)).json()["stale"] is False
    with owner_engine.begin() as conn:
        conn.execute(text("delete from message_projects"))
    assert (await translate(client, world.est)).json()["stale"] is True


async def test_thread_translations_lists_messages(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    with owner_engine.begin() as conn:
        reply = put_mail(
            conn,
            world.a,
            "re@a.example.jp",
            [world.fa],
            subject="Re: 【サンプルA】お見積りの送付",
            body="承知しました",
            day=3,
            irt="est@a.example.jp",
            refs=("est@a.example.jp",),
        )
    await process_user(worker, world.a)
    with owner_engine.begin() as conn:
        target = add_source(conn, world.a, "ImapMail/imap.example.com/INBOX.sbd/ProjectA")
        add_auto_run(conn, world.a, world.est, "done", target_source_id=target)
        add_auto_run(
            conn, world.a, reply, "failed", error_code="ai_bad_output", move_note="ambiguous"
        )
        thread = conn.scalar(text("select thread_id from messages where id = :m"), {"m": reply})
    llm.responses = [GOOD]
    await translate(client, world.est)
    r = await client.get(f"/api/v1/threads/{thread}/translations")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["message_id"] for i in items] == [world.est, reply]
    first, second = items
    assert first["translation"]["translated_body"].startswith("Số tiền")
    assert first["translation"]["stale"] is False
    assert first["auto"] == {
        "status": "done",
        "error_code": None,
        "move_note": None,
        "target_folder": "ImapMail/imap.example.com/INBOX.sbd/ProjectA",
    }
    assert second["translation"] is None
    assert second["auto"]["status"] == "failed"
    assert second["auto"]["error_code"] == "ai_bad_output"
    assert second["auto"]["target_folder"] is None


async def test_thread_without_anything(
    client: AsyncClient, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.connect() as conn:
        thread = conn.scalar(
            text("select thread_id from messages where id = :m"), {"m": world.other}
        )
    r = await client.get(f"/api/v1/threads/{thread}/translations")
    assert r.json() == {
        "items": [
            {
                "message_id": world.other,
                "translation": None,
                "auto": None,
                "projects": [],
                "move_projects": [],
            }
        ]
    }


async def test_thread_translations_move_projects_have_folder(
    client: AsyncClient, world: SimpleNamespace, owner_engine: Engine
) -> None:
    """Chọn dự án để chuyển chỉ liệt kê dự án của mail có folder; nhãn dự án vẫn đủ."""
    with owner_engine.begin() as conn:
        folder = add_source(conn, world.a, "ImapMail/imap.example.com/INBOX.sbd/P1")
        p1 = add_project(conn, world.a, "P1", processed_source_id=folder)
        p2 = add_project(conn, world.a, "P2")
        for p in (p1, p2):
            conn.execute(
                text(
                    "insert into message_projects (user_id, message_id, project_id) "
                    "values (:u, :m, :p)"
                ),
                {"u": world.a, "m": world.est, "p": p},
            )
        thread = conn.scalar(text("select thread_id from messages where id = :m"), {"m": world.est})
    r = await client.get(f"/api/v1/threads/{thread}/translations")
    item = r.json()["items"][0]
    assert [p["id"] for p in item["projects"]] == [p1, p2]
    assert item["move_projects"] == [{"id": p1, "name": "P1"}]


async def test_thread_of_other_user_404(
    client: AsyncClient, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        add_translation(conn, world.b, world.b_est, translated_body="SECRET-OF-B")
        thread = conn.scalar(
            text("select thread_id from messages where id = :m"), {"m": world.b_est}
        )
    r = await client.get(f"/api/v1/threads/{thread}/translations")
    assert (r.status_code, r.json()["code"]) == (404, "thread_not_found")
    assert "SECRET-OF-B" not in r.text


async def test_thread_translations_requires_login(app: FastAPI, world: SimpleNamespace) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/threads/1/translations")).status_code == 401


async def test_logs_have_no_mail_text(
    client: AsyncClient,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "お見積り", "bản-dịch-riêng", note="ghi-chú-riêng")
    llm.responses = [GOOD]
    with caplog.at_level("DEBUG"):
        assert (await translate(client, world.est)).status_code == 200
    for secret in ("田中", "見積", "サンプルA", "1,250,000", "bản-dịch-riêng", "ghi-chú-riêng"):
        assert secret not in caplog.text, secret
    assert "translate user=" in caplog.text


async def test_logs_have_no_key_or_full_url(
    real_gateway: Gateway, world: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    """Qua client thật, từ DB tới gateway: key và URL đầy đủ không vào log."""
    real_gateway.replies = [chat_reply(GOOD)]
    with caplog.at_level("DEBUG"):
        r = await translate(real_gateway.client, world.est)
    assert r.status_code == 200, r.text
    assert len(real_gateway.requests) == 1
    assert "CT-2026-0042" in real_gateway.requests[0].content.decode()
    for secret in ("田中", "見積", "1,250,000", GATEWAY_KEY, GATEWAY_URL, "secret-path"):
        assert secret not in caplog.text, secret
    assert "translate user=" in caplog.text


async def test_read_timeout_saves_nothing(
    real_gateway: Gateway, world: SimpleNamespace, owner_engine: Engine
) -> None:
    real_gateway.replies = [httpx.ReadTimeout("SECRET-TIMEOUT-TEXT")]
    r = await translate(real_gateway.client, world.est)
    assert (r.status_code, r.json()["code"]) == (503, "ai_timeout")
    assert r.json()["detail"] == TIMEOUT_MESSAGE
    assert "SECRET" not in r.text
    assert translation_count(owner_engine) == 0


async def test_thread_translations_projects_of_each_mail(
    client: AsyncClient, world: SimpleNamespace, owner_engine: Engine, worker: AsyncEngine
) -> None:
    """Mỗi mail chỉ có dự án của chính nó, sắp theo tên; dự án mail khác và người khác không lọt."""
    with owner_engine.begin() as conn:
        reply = put_mail(
            conn,
            world.a,
            "re@a.example.jp",
            [world.fa],
            subject="Re: 【サンプルA】お見積りの送付",
            body="承知しました",
            day=3,
            irt="est@a.example.jp",
            refs=("est@a.example.jp",),
        )
    await process_user(worker, world.a)
    with owner_engine.begin() as conn:
        link = text(
            "insert into message_projects (user_id, message_id, project_id) values (:u, :m, :p)"
        )
        p1 = add_project(conn, world.a, "P1")
        p3 = add_project(conn, world.a, "P3")
        p2 = add_project(conn, world.a, "P2")
        p_other = add_project(conn, world.a, "OTHER-MAIL-PROJECT")
        p_b = add_project(conn, world.b, "SECRET-OF-B")
        for m, p in ((world.est, p1), (reply, p3), (reply, p2), (world.other, p_other)):
            conn.execute(link, {"u": world.a, "m": m, "p": p})
        conn.execute(link, {"u": world.b, "m": world.b_est, "p": p_b})
        thread = conn.scalar(text("select thread_id from messages where id = :m"), {"m": reply})
    r = await client.get(f"/api/v1/threads/{thread}/translations")
    assert r.status_code == 200, r.text
    first, second = r.json()["items"]
    assert first["projects"] == [{"id": p1, "name": "P1"}]
    assert second["projects"] == [{"id": p2, "name": "P2"}, {"id": p3, "name": "P3"}]
    assert "OTHER-MAIL-PROJECT" not in r.text and "SECRET-OF-B" not in r.text
