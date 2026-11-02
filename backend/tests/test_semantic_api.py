"""Tìm kiếm hybrid (spec 19) và thread liên quan (spec 23) qua API, với ml-service giả.

Hai người dùng A, B có mail cùng chủ đề: kết quả của A không bao giờ có mail của B.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app import embedding
from app.config import embed_model
from app.db.session import create_api_engine, user_session
from app.main import create_app
from app.processing import process_user
from app.search import semantic
from app.search.keyword import MessageFilters
from app.search.query import parse_query
from app.search.semantic import (
    NEAREST_CHUNKS,
    exact_chunks_sql,
    rrf,
    scope_is_small,
    vector_chunks,
)
from tests.dbutil import (
    add_message_project,
    add_project,
    add_source,
    as_user,
    run_alembic,
    url_for_test_db,
)
from tests.mlfake import FakeMl, concept_vector
from tests.test_web_api import put_mail
from tests.webutil import add_web_user, login


def hnsw_indexdef(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("select indexdef from pg_indexes where indexname = 'ix_message_chunks_embedding'")
        ).scalar_one()


def test_hnsw_index_params(owner_engine: Engine, migrated: None) -> None:
    indexdef = hnsw_indexdef(owner_engine)
    assert "m='16'" in indexdef and "ef_construction='128'" in indexdef


def test_hnsw_migration_downgrades(owner_engine: Engine, clean: None) -> None:
    run_alembic("downgrade", "0019")
    try:
        indexdef = hnsw_indexdef(owner_engine)
        assert "hnsw" in indexdef and "ef_construction" not in indexdef
    finally:
        run_alembic("upgrade", "head")
    assert "ef_construction='128'" in hnsw_indexdef(owner_engine)


# --- hàm thuần ---


def test_any_term_query() -> None:
    q = parse_query('見積 "Project B" -請求', any_term=True)
    assert q is not None
    assert q.groonga == '("見積" OR "Project B") -"請求"'
    assert q.keywords == ("見積", "Project B")


def test_rrf() -> None:
    fused = rrf([[1, 2, 3], [3, 4]])
    assert [m for m, _ in fused] == [3, 1, 4, 2]  # 2 và 4 cùng hạng 2: id lớn hơn trước
    assert fused[0][1] == pytest.approx(1 / 63 + 1 / 61)
    assert [m for m, _ in rrf([[5], [6]])] == [6, 5]  # bằng điểm: id lớn hơn trước


# --- DB + API ---


@pytest.fixture
async def world(owner_engine: Engine, worker: AsyncEngine, clean: None) -> SimpleNamespace:
    a = add_web_user(owner_engine, "a@example.com")
    b = add_web_user(owner_engine, "b@example.com")
    with owner_engine.begin() as conn:
        fa = add_source(conn, a, "ImapMail/imap.example.com/INBOX")
        fa2 = add_source(conn, a, "ImapMail/imap.example.com/INBOX.sbd/ProjectA")
        fb = add_source(conn, b, "ImapMail/imap.example.com/INBOX")
        est = put_mail(
            conn,
            a,
            "est@a.example.jp",
            [fa2],
            subject="お見積りのお願い",
            body="在庫管理システムの見積をお願いします。",
            from_name="田中 健二",
            day=1,
        )
        est2 = put_mail(
            conn,
            a,
            "est2@a.example.jp",
            [fa2],
            subject="概算見積の件",
            body="追加の見積もお願いします。",
            from_name="佐藤 由紀",
            frm="sato@client-a.example.jp",
            day=40,
        )
        bug = put_mail(
            conn,
            a,
            "bug@a.example.jp",
            [fa],
            subject="【障害報告】入庫登録",
            body="本番環境でエラー E1024 が発生しています。",
            day=2,
        )
        other = put_mail(
            conn,
            a,
            "other@a.example.jp",
            [fa],
            subject="お知らせ",
            body="年末年始の休業日です。",
            day=3,
        )
        b_est = put_mail(
            conn,
            b,
            "b-est@b.example.jp",
            [fb],
            subject="見積",
            body="Bさんの見積です。見積 見積",
            day=1,
        )
    for u in (a, b):
        await process_user(worker, u)
        await embedding.embed_user(worker, FakeMl(), u)
    return SimpleNamespace(
        a=a, b=b, est=est, est2=est2, bug=bug, other=other, b_est=b_est, fa=fa, fa2=fa2
    )


@pytest.fixture
async def api() -> AsyncIterator[AsyncEngine]:
    engine = create_api_engine(url_for_test_db("api"))
    yield engine
    await engine.dispose()


@pytest.fixture
def ml() -> FakeMl:
    return FakeMl()


@pytest.fixture
async def client(
    api: AsyncEngine, ml: FakeMl, world: SimpleNamespace
) -> AsyncIterator[AsyncClient]:
    app = create_app(api, ml=ml, web_dist=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "a@example.com")
        yield c


async def hybrid(client: AsyncClient, **params: str | int) -> dict:
    r = await client.get("/api/v1/search", params={"mode": "hybrid", **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_hybrid_finds_by_meaning_across_languages(
    client: AsyncClient, world: SimpleNamespace
) -> None:
    data = await hybrid(client, q="khách hỏi báo giá")
    ids = [h["message_id"] for h in data["items"]]
    assert ids[:2] == [world.est2, world.est] or set(ids[:2]) == {world.est, world.est2}
    assert world.b_est not in ids
    assert data["mode"] == "hybrid" and data["embedding_pending"] == 0
    assert {h["match"] for h in data["items"][:2]} == {"semantic"}


async def test_hybrid_keyword_and_semantic_match(client: AsyncClient, world: SimpleNamespace):
    data = await hybrid(client, q="E1024 エラー")
    first = data["items"][0]
    assert first["message_id"] == world.bug and first["match"] == "both"
    assert any(s["hit"] and s["text"] == "E1024" for s in first["snippet"])


async def test_hybrid_filters_apply_to_vector_branch(client: AsyncClient, world: SimpleNamespace):
    data = await hybrid(client, q="báo giá", folder_id=world.fa)
    assert world.est not in [h["message_id"] for h in data["items"]]
    data = await hybrid(client, q="báo giá", sender="sato")
    assert [h["message_id"] for h in data["items"]] == [world.est2]


async def test_hybrid_pagination(client: AsyncClient, world: SimpleNamespace) -> None:
    full = await hybrid(client, q="báo giá")
    page = await hybrid(client, q="báo giá", offset=1, limit=1)
    assert page["total"] == full["total"]
    assert [h["message_id"] for h in page["items"]] == [full["items"][1]["message_id"]]


async def test_hybrid_errors(client: AsyncClient, ml: FakeMl) -> None:
    r = await client.get("/api/v1/search", params={"mode": "hybrid"})
    assert (r.status_code, r.json()["code"]) == (400, "query_required")
    ml.down = True
    r = await client.get("/api/v1/search", params={"mode": "hybrid", "q": "báo giá"})
    assert (r.status_code, r.json()["code"]) == (503, "ml_unavailable")
    ml.down, ml.model = False, "other/model"
    r = await client.get("/api/v1/search", params={"mode": "hybrid", "q": "báo giá"})
    assert (r.status_code, r.json()["code"]) == (503, "embedding_model_mismatch")


async def test_hybrid_ignores_stale_chunks_and_counts_pending(
    client: AsyncClient, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text("update messages set cleaner_version = cleaner_version + 1 where id = :m"),
            {"m": world.est},
        )
    data = await hybrid(client, q="báo giá")
    assert world.est not in [h["message_id"] for h in data["items"] if h["match"] != "keyword"]
    assert data["embedding_pending"] == 1


async def test_keyword_mode_unchanged(client: AsyncClient) -> None:
    r = await client.get("/api/v1/search", params={"q": "見積"})
    data = r.json()
    assert data["mode"] == "keyword" and data["embedding_pending"] is None
    assert all(h["match"] is None for h in data["items"])


def test_vector_query_uses_hnsw_index_under_rls(api_engine: Engine, world: SimpleNamespace):
    vec = "[" + ",".join(str(x) for x in concept_vector("見積")) + "]"
    with api_engine.begin() as conn:
        as_user(conn, world.a)
        # Bảng nhỏ: planner thích quét khóa chính (user_id) rồi sắp. Tắt sắp để chắc chắn index
        # HNSW dùng được trong phiên RLS; trên dữ liệu lớn đo bằng EXPLAIN ANALYZE (spec 19 mục 9).
        conn.execute(text("set local enable_seqscan = off"))
        conn.execute(text("set local enable_sort = off"))
        conn.execute(text("set local hnsw.iterative_scan = relaxed_order"))
        plan = "\n".join(
            conn.scalars(
                text("explain " + str(NEAREST_CHUNKS)),
                {"qv": vec, "model": "BAAI/bge-m3", "k": 300},
            )
        )
    assert "Index Scan using ix_message_chunks_embedding" in plan


# --- thread liên quan ---


async def test_similar_threads(client: AsyncClient, world: SimpleNamespace, owner_engine: Engine):
    with owner_engine.connect() as conn:
        threads = dict(
            conn.execute(
                text("select id, thread_id from messages where user_id = :u"), {"u": world.a}
            ).all()
        )
    r = await client.get(f"/api/v1/threads/{threads[world.est]}/similar")
    assert r.status_code == 200
    data = r.json()
    assert data["pending"] is False
    assert [i["thread_id"] for i in data["items"]] == [threads[world.est2]]
    assert data["items"][0]["message_id"] == world.est2
    assert data["items"][0]["score"] == pytest.approx(1.0, abs=1e-3)
    assert data["items"][0]["subject"] == "概算見積の件"
    # Thread không liên quan → không có gợi ý (dưới ngưỡng); thread của B → 404.
    r = await client.get(f"/api/v1/threads/{threads[world.other]}/similar")
    assert r.json()["items"] == []
    with owner_engine.connect() as conn:
        b_thread = conn.scalar(
            text("select thread_id from messages where id = :m"), {"m": world.b_est}
        )
    assert (await client.get(f"/api/v1/threads/{b_thread}/similar")).status_code == 404


async def test_similar_pending_without_embeddings(
    client: AsyncClient, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("delete from message_chunks where message_id = :m"), {"m": world.other})
        t = conn.scalar(text("select thread_id from messages where id = :m"), {"m": world.other})
    r = await client.get(f"/api/v1/threads/{t}/similar")
    assert r.json() == {"items": [], "pending": True}


# --- quét chính xác khi phạm vi nhỏ (spec 27 mục 4) ---

QV = concept_vector("見積")
EST_BODY = "見積の金額は100円です。"


@pytest.fixture
async def scope_world(owner_engine: Engine, worker: AsyncEngine, clean: None) -> SimpleNamespace:
    a = add_web_user(owner_engine, "a@example.com")
    b = add_web_user(owner_engine, "b@example.com")
    with owner_engine.begin() as conn:
        fa = add_source(conn, a, "ImapMail/imap.example.com/INBOX")
        fa2 = add_source(conn, a, "ImapMail/imap.example.com/INBOX.sbd/ProjectA")
        fb = add_source(conn, b, "ImapMail/imap.example.com/INBOX")
        crowd = [
            put_mail(conn, a, f"c{i}@a.example.jp", [fa], subject=f"見積{i}", body=EST_BODY)
            for i in range(30)
        ]
        far = put_mail(conn, a, "far@a.example.jp", [fa2], subject="別件", body="無関係な内容")
        near = put_mail(conn, a, "near@a.example.jp", [fa], subject="見積近", body=EST_BODY)
        stale = put_mail(conn, a, "stale@a.example.jp", [fa], subject="見積古", body=EST_BODY)
        wrong = put_mail(conn, a, "wrong@a.example.jp", [fa], subject="見積別", body=EST_BODY)
        b_est = put_mail(conn, b, "b@b.example.jp", [fb], subject="見積", body=EST_BODY)
    for u in (a, b):
        await process_user(worker, u)
        await embedding.embed_user(worker, FakeMl(), u)
    # Link dự án sau mọi process_user: bước gán của worker xóa link tay (spec 25).
    with owner_engine.begin() as conn:
        small = add_project(conn, a, "Small")
        for m in (far, near, stale, wrong):
            add_message_project(conn, a, m, small)
        conn.execute(
            text("update messages set cleaner_version = cleaner_version + 1 where id = :m"),
            {"m": stale},
        )
        conn.execute(
            text("update message_chunks set model = 'old/model' where message_id = :m"),
            {"m": wrong},
        )
    return SimpleNamespace(
        a=a, b=b, crowd=crowd, far=far, near=near, stale=stale, wrong=wrong, b_est=b_est,
        fa2=fa2, small=small,
    )  # fmt: skip


@pytest.fixture
async def api_session(
    api: AsyncEngine, scope_world: SimpleNamespace
) -> AsyncIterator[AsyncSession]:
    async with user_session(api, scope_world.a) as s:
        yield s


async def hit_ids(s: AsyncSession, f: MessageFilters, **kw: Any) -> set[int]:
    return {h.message_id for h in await vector_chunks(s, QV, f, limit=5, **kw)}


async def test_exact_scan_finds_what_hnsw_misses(
    api_session: AsyncSession, scope_world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semantic, "MAX_CANDIDATES", 5)
    f = MessageFilters(project_id=scope_world.small)
    assert await scope_is_small(api_session, f) is True
    assert scope_world.far in await hit_ids(api_session, f)
    monkeypatch.setattr(semantic, "EXACT_SCAN_MAX_CHUNKS", 0)
    assert await scope_is_small(api_session, f) is False
    assert scope_world.far not in await hit_ids(api_session, f)


async def test_no_filter_does_not_count(
    api_session: AsyncSession, scope_world: SimpleNamespace
) -> None:
    assert await scope_is_small(api_session, MessageFilters()) is False


async def test_exact_scan_respects_stale_and_rls(
    api_session: AsyncSession, scope_world: SimpleNamespace
) -> None:
    w = scope_world
    got = {
        h.message_id
        for h in await vector_chunks(
            api_session, QV, MessageFilters(has_attachments=False), limit=500, exact=True
        )
    }
    assert {w.near, w.far, *w.crowd} <= got
    assert not ({w.stale, w.wrong, w.b_est} & got)
    in_project = {
        h.message_id
        for h in await vector_chunks(
            api_session, QV, MessageFilters(project_id=w.small), limit=500, exact=True
        )
    }
    assert in_project == {w.near, w.far}


async def test_exact_scan_excludes_thread(
    api_session: AsyncSession, scope_world: SimpleNamespace
) -> None:
    f = MessageFilters(project_id=scope_world.small)
    thread = await api_session.scalar(
        text("select thread_id from messages where id = :m"), {"m": scope_world.near}
    )
    got = {
        h.message_id
        for h in await vector_chunks(
            api_session, QV, f, limit=50, exact=True, exclude_thread_id=thread
        )
    }
    assert got == {scope_world.far}


async def test_project_and_folder_intersect_on_both_paths(
    api_session: AsyncSession, scope_world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = MessageFilters(project_id=scope_world.small, folder_id=scope_world.fa2)
    results = []
    for cap in (semantic.EXACT_SCAN_MAX_CHUNKS, 0):
        monkeypatch.setattr(semantic, "EXACT_SCAN_MAX_CHUNKS", cap)
        hits = await vector_chunks(api_session, QV, f, limit=50)
        results.append({h.message_id for h in hits})
    assert results == [{scope_world.far}, {scope_world.far}]


def test_exact_query_does_not_use_hnsw(api_engine: Engine, scope_world: SimpleNamespace) -> None:
    vec = "[" + ",".join(str(x) for x in QV) + "]"
    params = {"qv": vec, "model": embed_model(), "k": 10}
    with api_engine.begin() as conn:
        as_user(conn, scope_world.a)
        # Cùng cài đặt với test HNSW ở trên: bảng nhỏ nên phải tắt seq scan và sort thì planner
        # mới chịu dùng index. Đối chứng: câu HNSW dùng index, câu quét chính xác thì không.
        conn.execute(text("set local enable_seqscan = off"))
        conn.execute(text("set local enable_sort = off"))
        conn.execute(text("set local hnsw.iterative_scan = relaxed_order"))
        control = "\n".join(conn.scalars(text("explain " + str(NEAREST_CHUNKS)), params))
        plan = "\n".join(
            conn.scalars(
                text("explain " + str(exact_chunks_sql(["m.has_attachments = :ha"]))),
                {**params, "ha": False},
            )
        )
    assert "ix_message_chunks_embedding" in control
    assert "ix_message_chunks_embedding" not in plan
