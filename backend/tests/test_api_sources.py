"""Danh sách folder trên server và xóa mail của một folder (spec xóa mail của folder)."""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text

from app.cli import create_user
from app.db.session import create_api_engine
from app.main import create_app
from tests.dbutil import add_auto_run, add_message, url_for_test_db
from tests.test_api_ingest import INBOX, PROJECT, batch, bearer, mail


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_api_engine(url_for_test_db("api"))
    async with AsyncClient(
        transport=ASGITransport(app=create_app(engine)), base_url="http://test"
    ) as c:
        yield c
    await engine.dispose()


@pytest.fixture
async def data(client: AsyncClient, owner_engine: Engine, clean: None) -> SimpleNamespace:
    """A: INBOX {m1, m2}, ProjectA {m2, m3}, thêm một mail mồ côi; B: INBOX {m1} (bản riêng)."""
    with owner_engine.begin() as conn:
        a, token_a = create_user(conn, "a@example.com", "A")
        b, token_b = create_user(conn, "b@example.com", "B")
        orphan = add_message(conn, "orphan-hash", user_id=a)
    for token, msgs, folder in (
        (token_a, [mail(1), mail(2)], INBOX),
        (token_a, [mail(2), mail(3)], PROJECT),
        (token_b, [mail(1)], INBOX),
    ):
        r = await client.post("/api/v1/ingest", headers=bearer(token), json=batch(msgs, folder))
        assert r.status_code == 200, r.text
    return SimpleNamespace(a=a, b=b, token_a=token_a, token_b=token_b, orphan=orphan)


def message_ids(owner_engine: Engine, user_id: int) -> set[str | None]:
    with owner_engine.connect() as conn:
        return set(
            conn.scalars(
                text(
                    "select coalesce(message_id, message_id_hash) from messages where user_id = :u"
                ),
                {"u": user_id},
            )
        )


async def test_list_sources(client: AsyncClient, data: SimpleNamespace) -> None:
    r = await client.get("/api/v1/sources", headers=bearer(data.token_a))
    assert r.status_code == 200
    got = [(s["folder_path"], s["message_count"], s["kind"]) for s in r.json()]
    assert got == [(INBOX, 2, "imap_cache"), (PROJECT, 2, "imap_cache")]
    r = await client.get("/api/v1/sources", headers=bearer(data.token_b))
    assert [s["folder_path"] for s in r.json()] == [INBOX]


async def test_delete_folder_mail(
    client: AsyncClient, owner_engine: Engine, data: SimpleNamespace
) -> None:
    r = await client.post(
        "/api/v1/sources/delete-mail", headers=bearer(data.token_a), json={"folder_path": INBOX}
    )
    assert r.status_code == 200, r.text
    # m1 chỉ ở INBOX → xóa; m2 còn ở ProjectA → giữ; mail mồ côi không thuộc INBOX → giữ.
    assert r.json() == {"deleted_messages": 1, "kept_messages": 1}
    assert message_ids(owner_engine, data.a) == {
        "msg2@client-a.example.jp",
        "msg3@client-a.example.jp",
        "orphan-hash",
    }
    r = await client.get("/api/v1/sources", headers=bearer(data.token_a))
    assert [(s["folder_path"], s["message_count"]) for s in r.json()] == [(PROJECT, 2)]
    # Bản của B không bị đụng tới.
    assert message_ids(owner_engine, data.b) == {"msg1@client-a.example.jp"}


async def test_delete_unknown_folder_404(client: AsyncClient, data: SimpleNamespace) -> None:
    r = await client.post(
        "/api/v1/sources/delete-mail",
        headers=bearer(data.token_a),
        json={"folder_path": "Mail/Local Folders/Nope"},
    )
    assert r.status_code == 404
    # Mã riêng để collector phân biệt "folder không có" với 404 của proxy / sai đường dẫn.
    assert r.json()["code"] == "source_not_found"
    # A xóa INBOX của mình hai lần: lần hai 404, INBOX của B vẫn còn.
    await client.post(
        "/api/v1/sources/delete-mail", headers=bearer(data.token_a), json={"folder_path": INBOX}
    )
    r = await client.post(
        "/api/v1/sources/delete-mail", headers=bearer(data.token_a), json={"folder_path": INBOX}
    )
    assert r.status_code == 404
    r = await client.get("/api/v1/sources", headers=bearer(data.token_b))
    assert [s["message_count"] for s in r.json()] == [1]


async def test_sources_require_token(client: AsyncClient, data: SimpleNamespace) -> None:
    assert (await client.get("/api/v1/sources")).status_code == 401
    r = await client.post("/api/v1/sources/delete-mail", json={"folder_path": INBOX})
    assert r.status_code == 401
    # Xác thực trước khi đọc body: client chưa xác thực không làm server đọc/parse body.
    r = await client.post(
        "/api/v1/sources/delete-mail",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401


async def test_sources_route_counts_physical_after_move(
    client: AsyncClient, owner_engine: Engine, data: SimpleNamespace
) -> None:
    """Mail đã chuyển trên web (spec 26) vẫn tính ở folder vật lý trong /sources của collector."""
    with owner_engine.begin() as conn:
        inbox, project = (
            conn.scalar(
                text("select id from mail_sources where user_id = :u and folder_path = :p"),
                {"u": data.a, "p": path},
            )
            for path in (INBOX, PROJECT)
        )
        m1 = conn.scalar(
            text("select id from messages where user_id = :u and message_id like 'msg1@%'"),
            {"u": data.a},
        )
        add_auto_run(conn, data.a, m1, "done", from_source_id=inbox, target_source_id=project)
    r = await client.get("/api/v1/sources", headers=bearer(data.token_a))
    assert r.status_code == 200
    got = [(s["folder_path"], s["message_count"]) for s in r.json()]
    assert got == [(INBOX, 2), (PROJECT, 2)]
