# ruff: noqa: F811
"""Folder hiệu lực và API chuyển mail (spec 26 mục 8). Dữ liệu giả, AI giả.

Mail `est` của A nằm ở Inbox (ownership vật lý), có run `done` chuyển tới folder dự án P.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auto_translate import next_message
from app.cli import create_token
from app.db.session import user_session
from app.search.keyword import MessageFilters, folders_of, search_messages
from app.search.query import parse_query
from app.search.semantic import hybrid_search
from app.sources import list_folders, list_sources
from tests.dbutil import add_auto_run, add_ownership, add_project, add_source, add_translation
from tests.mlfake import FakeLlm, concept_vector
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
from tests.webutil import CSRF

PROJECT_PATH = "ImapMail/imap.example.com/INBOX.sbd/ProjectA"
OTHER_PATH = "ImapMail/imap.example.com/INBOX.sbd/Other"


@pytest.fixture
def moved(world: SimpleNamespace, owner_engine: Engine) -> SimpleNamespace:
    """est đã chuyển tới folder P; other ở lại Inbox. Dự án chứa est và có folder P."""
    with owner_engine.begin() as conn:
        p = add_source(conn, world.a, PROJECT_PATH)
        project = add_project(
            conn, world.a, "P", customer_domains=["a.example.jp"], processed_source_id=p
        )
        conn.execute(
            text(
                "insert into message_projects (user_id, message_id, project_id) values (:u,:m,:p)"
            ),
            {"u": world.a, "m": world.est, "p": project},
        )
        add_auto_run(conn, world.a, world.est, "done", from_source_id=world.fa, target_source_id=p)
    return SimpleNamespace(**vars(world), p=p, project=project)


def ids(result: Any) -> set[int]:
    return {h.message_id for h in result.items}


async def keyword_ids(api: AsyncEngine, user: int, folder: int) -> set[int]:
    async with user_session(api, user) as s:
        r = await search_messages(s, user, parse_query("見積 OR お知らせ"), MessageFilters(folder))
    return ids(r)


async def test_filter_inbox_hides_moved(api: AsyncEngine, moved: SimpleNamespace) -> None:
    assert await keyword_ids(api, moved.a, moved.fa) == {moved.other}


async def test_filter_project_folder_shows_moved(api: AsyncEngine, moved: SimpleNamespace) -> None:
    assert await keyword_ids(api, moved.a, moved.p) == {moved.est}


async def test_hybrid_search_uses_effective_folder(
    api: AsyncEngine, moved: SimpleNamespace
) -> None:
    vec = concept_vector("見積")
    async with user_session(api, moved.a) as s:
        in_inbox = await hybrid_search(s, vec, moved.a, None, MessageFilters(moved.fa))
        in_project = await hybrid_search(s, vec, moved.a, None, MessageFilters(moved.p))
    assert moved.est not in ids(in_inbox)
    assert ids(in_project) == {moved.est}


async def test_threads_list_by_folder(client: AsyncClient, moved: SimpleNamespace) -> None:
    inbox = await client.get(f"/api/v1/threads?folder_id={moved.fa}")
    project = await client.get(f"/api/v1/threads?folder_id={moved.p}")
    assert inbox.status_code == project.status_code == 200
    assert len(project.json()["items"]) == 1
    assert len(inbox.json()["items"]) == 1
    assert project.json()["items"][0]["id"] != inbox.json()["items"][0]["id"]


async def test_mail_in_second_real_folder_stays_there(
    api: AsyncEngine, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        f2 = add_source(conn, moved.a, OTHER_PATH)
        add_ownership(conn, moved.est, moved.a, f2, mbox_offset=7)
    assert moved.est in await keyword_ids(api, moved.a, f2)
    assert moved.est not in await keyword_ids(api, moved.a, moved.fa)
    async with user_session(api, moved.a) as s:
        folders = await folders_of(s, [moved.est])
    assert {f.path for f in folders[moved.est]} == {PROJECT_PATH, OTHER_PATH}


SECOND_INBOX = "ImapMail/imap2.example.com/INBOX"


@pytest.fixture
def two_inboxes(moved: SimpleNamespace, owner_engine: Engine) -> SimpleNamespace:
    """est có ownership ở Inbox của tài khoản thứ hai; run chỉ ghi được một from_source_id."""
    with owner_engine.begin() as conn:
        inbox2 = add_source(conn, moved.a, SECOND_INBOX, account="imap2.example.com")
        add_ownership(conn, moved.est, moved.a, inbox2, mbox_offset=8)
    return SimpleNamespace(**vars(moved), inbox2=inbox2)


async def test_moved_mail_leaves_every_inbox(
    api: AsyncEngine, two_inboxes: SimpleNamespace
) -> None:
    w = two_inboxes
    assert await keyword_ids(api, w.a, w.inbox2) == set()
    assert await keyword_ids(api, w.a, w.fa) == {w.other}
    assert await keyword_ids(api, w.a, w.p) == {w.est}


async def test_folders_of_hides_every_inbox(api: AsyncEngine, two_inboxes: SimpleNamespace) -> None:
    async with user_session(api, two_inboxes.a) as s:
        folders = await folders_of(s, [two_inboxes.est])
    assert [f.path for f in folders[two_inboxes.est]] == [PROJECT_PATH]


async def test_get_folders_counts_every_inbox(
    client: AsyncClient, two_inboxes: SimpleNamespace
) -> None:
    counts = {
        f["folder_path"]: f["message_count"] for f in (await client.get("/api/v1/folders")).json()
    }
    assert counts == {"ImapMail/imap.example.com/INBOX": 1, SECOND_INBOX: 0, PROJECT_PATH: 1}


async def test_move_back_returns_mail_to_every_inbox(
    client: AsyncClient, api: AsyncEngine, two_inboxes: SimpleNamespace
) -> None:
    w = two_inboxes
    await post(client, f"/api/v1/messages/{w.est}/move-back")
    assert await keyword_ids(api, w.a, w.inbox2) == {w.est}
    assert await keyword_ids(api, w.a, w.fa) == {w.est, w.other}


async def test_inbox_subfolder_is_not_an_inbox(
    api: AsyncEngine, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    """Quy tắc mới chỉ áp cho Inbox: folder thường vẫn giữ mail dù mail đã chuyển."""
    with owner_engine.begin() as conn:
        f2 = add_source(conn, moved.a, OTHER_PATH)
        add_ownership(conn, moved.est, moved.a, f2, mbox_offset=7)
    assert moved.est in await keyword_ids(api, moved.a, f2)


async def test_get_folders_counts_mail_really_in_target_once(
    client: AsyncClient, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    """Mail vừa nằm thật ở folder đích vừa có run trỏ tới: một mail, không đếm đôi."""
    with owner_engine.begin() as conn:
        add_ownership(conn, moved.est, moved.a, moved.p, mbox_offset=9)
    counts = {
        f["folder_path"]: f["message_count"] for f in (await client.get("/api/v1/folders")).json()
    }
    assert counts == {"ImapMail/imap.example.com/INBOX": 1, PROJECT_PATH: 1}


async def test_get_folders_counts_deleted_ownership_in_target_as_run(
    client: AsyncClient, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    """Ownership đã xóa không phải bản còn sống: run vẫn đếm mail ở folder đích."""
    with owner_engine.begin() as conn:
        add_ownership(conn, moved.est, moved.a, moved.p, mbox_offset=9)
        conn.execute(
            text("update message_ownership set is_deleted = true where source_id = :s"),
            {"s": moved.p},
        )
    counts = {
        f["folder_path"]: f["message_count"] for f in (await client.get("/api/v1/folders")).json()
    }
    assert counts[PROJECT_PATH] == 1


async def test_folders_of_effective(api: AsyncEngine, moved: SimpleNamespace) -> None:
    async with user_session(api, moved.a) as s:
        folders = await folders_of(s, [moved.est, moved.other])
    assert [(f.id, f.path) for f in folders[moved.est]] == [(moved.p, PROJECT_PATH)]
    assert [f.id for f in folders[moved.other]] == [moved.fa]


async def test_get_folders_counts_effective(client: AsyncClient, moved: SimpleNamespace) -> None:
    r = await client.get("/api/v1/folders")
    assert r.status_code == 200
    counts = {f["folder_path"]: f["message_count"] for f in r.json()}
    assert counts == {"ImapMail/imap.example.com/INBOX": 1, PROJECT_PATH: 1}


async def test_collector_sources_counts_physical(api: AsyncEngine, moved: SimpleNamespace) -> None:
    async with user_session(api, moved.a) as s:
        physical = {f.folder_path: f.message_count for f in await list_sources(s)}
        effective = {f.folder_path: f.message_count for f in await list_folders(s)}
    assert physical == {"ImapMail/imap.example.com/INBOX": 2, PROJECT_PATH: 0}
    assert effective == {"ImapMail/imap.example.com/INBOX": 1, PROJECT_PATH: 1}


async def test_resync_keeps_move(
    api: AsyncEngine, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    # Ingest lại chỉ chạm ownership (upsert): giả lập bằng cập nhật ownership vật lý.
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "update message_ownership set mbox_offset = 999, is_deleted = false "
                "where message_id = :m"
            ),
            {"m": moved.est},
        )
    assert await keyword_ids(api, moved.a, moved.fa) == {moved.other}
    assert await keyword_ids(api, moved.a, moved.p) == {moved.est}


def run_of(owner: Engine, message: int) -> Any:
    with owner.connect() as conn:
        return conn.execute(
            text("select * from message_auto_runs where message_id = :m"), {"m": message}
        ).first()


async def post(client: AsyncClient, path: str, **body: Any):
    return await client.post(path, json=body or None, headers=CSRF)


async def test_move_back_returns_to_inbox_and_stays(
    client: AsyncClient, api: AsyncEngine, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    r = await post(client, f"/api/v1/messages/{moved.est}/move-back")
    assert r.status_code == 200, r.text
    assert await keyword_ids(api, moved.a, moved.fa) == {moved.est, moved.other}
    assert await keyword_ids(api, moved.a, moved.p) == set()
    row = run_of(owner_engine, moved.est)
    assert row.status == "done" and row.target_source_id is None
    async with user_session(api, moved.a) as s:
        picked = await next_message(s, moved.a)
    assert picked is None or picked[0] != moved.est


async def test_moved_back_mail_can_be_moved_again(
    client: AsyncClient, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    await post(client, f"/api/v1/messages/{moved.est}/move-back")
    r = await post(client, f"/api/v1/messages/{moved.est}/move", project_id=moved.project)
    assert r.status_code == 200, r.text
    assert run_of(owner_engine, moved.est).target_source_id == moved.p


@pytest.fixture
def ambiguous(moved: SimpleNamespace, owner_engine: Engine) -> SimpleNamespace:
    """other: ambiguous, thuộc hai dự án (một có folder, một không có folder)."""
    with owner_engine.begin() as conn:
        nofolder = add_project(conn, moved.a, "NoFolder", customer_domains=["a.example.jp"])
        for pid in (moved.project, nofolder):
            conn.execute(
                text(
                    "insert into message_projects (user_id, message_id, project_id) "
                    "values (:u,:m,:p)"
                ),
                {"u": moved.a, "m": moved.other, "p": pid},
            )
        add_auto_run(
            conn, moved.a, moved.other, "done", from_source_id=moved.fa, move_note="ambiguous"
        )
        outsider = add_project(conn, moved.a, "Outsider", processed_source_id=moved.p)
    return SimpleNamespace(**vars(moved), nofolder=nofolder, outsider=outsider)


async def test_move_requires_project_of_mail_with_folder(
    client: AsyncClient, ambiguous: SimpleNamespace, owner_engine: Engine
) -> None:
    url = f"/api/v1/messages/{ambiguous.other}/move"
    for bad in (ambiguous.nofolder, ambiguous.outsider, 999_999):
        r = await post(client, url, project_id=bad)
        assert (r.status_code, r.json()["code"]) == (422, "move_invalid"), bad
    row = run_of(owner_engine, ambiguous.other)
    assert row.target_source_id is None and row.move_note == "ambiguous"
    r = await post(client, url, project_id=ambiguous.project)
    assert r.status_code == 200, r.text
    row = run_of(owner_engine, ambiguous.other)
    assert row.target_source_id == ambiguous.p and row.move_note is None


async def test_move_needs_done_run(
    client: AsyncClient, ambiguous: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text("delete from message_auto_runs where message_id = :m"), {"m": ambiguous.other}
        )
    r = await post(client, f"/api/v1/messages/{ambiguous.other}/move", project_id=ambiguous.project)
    assert (r.status_code, r.json()["code"]) == (409, "move_not_ready")


async def test_auto_retry_only_failed(
    client: AsyncClient, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        add_auto_run(conn, moved.a, moved.other, "failed", error_code="ai_bad_output")
    r = await post(client, f"/api/v1/messages/{moved.other}/auto-retry")
    assert r.status_code == 204, r.text
    assert run_of(owner_engine, moved.other) is None
    for message in (moved.other, moved.est):  # không còn dòng / dòng done
        r = await post(client, f"/api/v1/messages/{message}/auto-retry")
        assert (r.status_code, r.json()["code"]) == (409, "auto_retry_invalid")
    assert run_of(owner_engine, moved.est).status == "done"


async def test_delete_target_folder_returns_mail(
    api: AsyncEngine, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    from app.sources import delete_folder_mail

    async with user_session(api, moved.a) as s:
        result = await delete_folder_mail(s, moved.a, PROJECT_PATH)
    assert result is not None and result.deleted_messages == 0
    assert await keyword_ids(api, moved.a, moved.fa) == {moved.est, moved.other}
    assert run_of(owner_engine, moved.est).target_source_id is None


async def test_delete_inbox_deletes_moved_mail(api: AsyncEngine, moved: SimpleNamespace) -> None:
    from app.sources import delete_folder_mail

    async with user_session(api, moved.a) as s:
        result = await delete_folder_mail(s, moved.a, "ImapMail/imap.example.com/INBOX")
    assert result is not None and result.deleted_messages == 2
    async with user_session(api, moved.a) as s:
        assert await s.scalar(text("select count(*) from messages")) == 0


async def test_delete_folder_api_removes_translation(
    api: AsyncEngine, app: Any, moved: SimpleNamespace, owner_engine: Engine
) -> None:
    """Điều kiện của ngoại lệ lưu chữ dịch (spec mục 10): xóa mail của folder làm mất bản dịch."""
    with owner_engine.begin() as conn:
        add_translation(conn, moved.a, moved.est)
        add_translation(conn, moved.a, moved.other)
        token = create_token(conn, moved.a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as collector:
        r = await collector.post(
            "/api/v1/sources/delete-mail",
            headers={"Authorization": f"Bearer {token}"},
            json={"folder_path": "ImapMail/imap.example.com/INBOX"},
        )
    assert r.status_code == 200, r.text
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from message_translations")) == 0


async def test_other_user_404(
    client: AsyncClient, moved: SimpleNamespace, ambiguous: SimpleNamespace
) -> None:
    for path, body in (
        (f"/api/v1/messages/{moved.b_est}/move-back", {}),
        (f"/api/v1/messages/{moved.b_est}/move", {"project_id": moved.project}),
        (f"/api/v1/messages/{moved.b_est}/auto-retry", {}),
    ):
        r = await post(client, path, **body)
        assert (r.status_code, r.json()["code"]) == (404, "message_not_found"), path


async def test_requires_csrf_header(client: AsyncClient, moved: SimpleNamespace) -> None:
    for path in ("move-back", "auto-retry"):
        r = await client.post(f"/api/v1/messages/{moved.est}/{path}")
        assert r.status_code == 403


async def test_ask_filters_by_effective_folder(
    client: AsyncClient, llm: FakeLlm, moved: SimpleNamespace
) -> None:
    def source_ids(data: Any) -> list[int]:
        return [s["message_id"] for s in data["sources"]]

    llm.responses = [plan(), ANSWER]
    in_inbox = (await ask(client, folder_id=moved.fa)).json()
    assert moved.est not in source_ids(in_inbox)
    llm.responses = [plan(), ANSWER]
    in_project = (await ask(client, folder_id=moved.p)).json()
    assert source_ids(in_project) == [moved.est]
