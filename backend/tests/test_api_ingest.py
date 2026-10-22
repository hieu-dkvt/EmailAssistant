"""Ingest API: ghi lô mail vào DB của đúng người dùng, idempotent, có giới hạn kích thước."""

import gzip
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text

from app.cli import create_user
from app.db.session import create_api_engine
from app.main import Limits, create_app
from tests.dbutil import url_for_test_db

INBOX = "ImapMail/imap.example.com/INBOX"
PROJECT = "ImapMail/imap.example.com/INBOX.sbd/ProjectA"


@pytest.fixture
async def engine_and_limits() -> AsyncIterator[dict[str, Any]]:
    engine = create_api_engine(url_for_test_db("api"))
    yield {"engine": engine}
    await engine.dispose()


async def make_client(engine: Any, **limits: int) -> AsyncClient:
    app = create_app(
        engine,
        max_body_bytes=limits.get("max_body_bytes", Limits.max_body_bytes),
        max_decompressed_bytes=limits.get("max_decompressed_bytes", Limits.max_decompressed_bytes),
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(engine_and_limits: dict[str, Any]) -> AsyncIterator[AsyncClient]:
    async with await make_client(engine_and_limits["engine"]) as c:
        yield c


@pytest.fixture
def users(owner_engine: Engine, clean: None) -> SimpleNamespace:
    with owner_engine.begin() as conn:
        a, token_a = create_user(conn, "a@example.com", "A")
        b, token_b = create_user(conn, "b@example.com", "B")
    return SimpleNamespace(a=a, b=b, token_a=token_a, token_b=token_b)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def mail(i: int, **over: Any) -> dict[str, Any]:
    m: dict[str, Any] = {
        "message_id": f"<msg{i}@client-a.example.jp>",
        "subject": f"【サンプル】件名 {i}",
        "from": {"name": "田中 健二", "addr": "Tanaka@Client-A.example.jp"},
        "to": [{"name": "Trần Hiếu", "addr": "pm@vn-outsource.example.com"}],
        "cc": [],
        "date": "2026-09-01T10:00:00+09:00",
        "date_tz_offset_min": 540,
        "in_reply_to": None,
        "references": [],
        "thread_index": None,
        "body_text": f"本文 {i}",
        "body_from_html": False,
        "attachments": [],
        "mbox_offset": i * 1000,
        "flags": {"read": True, "replied": False, "flagged": False},
    }
    m.update(over)
    return m


PARSER = "collector/0.4.0"


def batch(messages: list[dict[str, Any]], folder: str = INBOX) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "parser_version": PARSER,
        "source": {
            "folder_path": folder,
            "account": "imap.example.com",
            "kind": "imap_cache",
            "file_size": 5000,
            "last_offset": 4000,
            "last_mtime": "2026-10-17T09:00:00+07:00",
        },
        "messages": messages,
    }


def counts(owner_engine: Engine, user_id: int) -> tuple[int, int, int]:
    with owner_engine.connect() as conn:
        return (
            conn.scalar(text("select count(*) from messages where user_id = :u"), {"u": user_id}),
            conn.scalar(
                text("select count(*) from message_ownership where user_id = :u"), {"u": user_id}
            ),
            conn.scalar(
                text("select count(*) from mail_sources where user_id = :u"), {"u": user_id}
            ),
        )


async def test_ingest_writes_rows(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    att = [{"filename": "見積.xlsx", "size": 1234, "content_type": "application/vnd.ms-excel"}]
    msgs = [
        mail(1),
        mail(
            2, references=["<msg1@client-a.example.jp>"], in_reply_to="<msg1@client-a.example.jp>"
        ),
        mail(3, attachments=att, body_from_html=True, flags={"read": False, "replied": True}),
    ]
    r = await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch(msgs))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {k: body[k] for k in ("received", "inserted", "duplicates", "mismatched")} == {
        "received": 3,
        "inserted": 3,
        "duplicates": 0,
        "mismatched": 0,
    }
    assert counts(owner_engine, users.a) == (3, 3, 1)

    with owner_engine.connect() as conn:
        m3 = conn.execute(
            text(
                "select m.subject, m.from_addr, m.from_name, m.to_addrs, m.sent_tz_offset_min, "
                "m.has_attachments, m.attachments, m.body_from_html, m.parser_version, "
                "o.mbox_offset, o.is_read, o.is_replied, s.folder_path, s.last_offset "
                "from messages m join message_ownership o on o.message_id = m.id "
                "join mail_sources s on s.id = o.source_id "
                "where m.message_id = 'msg3@client-a.example.jp'"
            )
        ).one()
        m2 = conn.execute(
            text(
                "select in_reply_to, references_ids from messages "
                "where message_id = 'msg2@client-a.example.jp'"
            )
        ).one()
    assert m3.subject == "【サンプル】件名 3"
    assert m3.from_addr == "tanaka@client-a.example.jp"
    assert m3.from_name == "田中 健二"
    assert m3.to_addrs == [{"name": "Trần Hiếu", "addr": "pm@vn-outsource.example.com"}]
    assert m3.sent_tz_offset_min == 540
    assert m3.has_attachments is True and m3.attachments == att
    assert m3.body_from_html is True and m3.parser_version == PARSER
    assert (m3.mbox_offset, m3.is_read, m3.is_replied) == (3000, False, True)
    assert (m3.folder_path, m3.last_offset) == (INBOX, 4000)
    assert m2.in_reply_to == "msg1@client-a.example.jp"
    assert m2.references_ids == ["msg1@client-a.example.jp"]


async def test_ingest_is_idempotent(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    msgs = [mail(1), mail(2)]
    await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch(msgs))
    # Gửi lại (vd. sau compact: offset đổi) → không tạo dòng mới, offset được cập nhật.
    msgs2 = [mail(1, mbox_offset=7), mail(2, mbox_offset=8)]
    r = await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch(msgs2))
    assert (r.json()["inserted"], r.json()["duplicates"]) == (0, 2)
    assert counts(owner_engine, users.a) == (2, 2, 1)
    with owner_engine.connect() as conn:
        offsets = set(conn.scalars(text("select mbox_offset from message_ownership")))
    assert offsets == {7, 8}


async def test_same_mail_in_two_folders_is_one_copy(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch([mail(1)]))
    r = await client.post(
        "/api/v1/ingest", headers=bearer(users.token_a), json=batch([mail(1)], PROJECT)
    )
    assert r.json()["duplicates"] == 1
    assert counts(owner_engine, users.a) == (1, 2, 2)


async def test_two_users_get_separate_copies(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch([mail(1)]))
    r = await client.post("/api/v1/ingest", headers=bearer(users.token_b), json=batch([mail(1)]))
    assert r.json()["inserted"] == 1
    assert counts(owner_engine, users.a) == (1, 1, 1)
    assert counts(owner_engine, users.b) == (1, 1, 1)


async def test_duplicate_within_batch_and_without_message_id(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    no_id = mail(5, message_id=None)
    r = await client.post(
        "/api/v1/ingest",
        headers=bearer(users.token_a),
        json=batch([mail(1), mail(1, mbox_offset=99), no_id, dict(no_id, mbox_offset=98)]),
    )
    assert (r.json()["inserted"], r.json()["duplicates"]) == (2, 2)
    assert counts(owner_engine, users.a)[0] == 2


async def test_mismatch_keeps_first_copy(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch([mail(1)]))
    fake = mail(
        1,
        **{"from": {"name": "x", "addr": "attacker@evil.example"}},
        body_text="giả",
        mbox_offset=777,
    )
    r = await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch([fake]))
    assert (r.json()["duplicates"], r.json()["mismatched"]) == (1, 1)
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select body_text from messages")) == "本文 1"
        # Vị trí trong mbox vẫn trỏ tới bản thật ("xem bản gốc" không mở ra mail giả).
        assert conn.scalar(text("select mbox_offset from message_ownership")) == 1000


async def test_out_of_range_date_becomes_null(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    odd = mail(9, message_id=None, date="0001-01-01T00:00:00+14:00")
    r = await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=batch([odd]))
    assert r.status_code == 200, r.text
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select sent_at from messages")) is None


async def test_huge_integers_rejected_not_500(client: AsyncClient, users: SimpleNamespace) -> None:
    r = await client.post(
        "/api/v1/ingest", headers=bearer(users.token_a), json=batch([mail(1, mbox_offset=2**63)])
    )
    assert r.status_code == 422


async def test_gzip_and_nul_stripped(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace
) -> None:
    payload = gzip.compress(
        json.dumps(batch([mail(1, subject="a\x00b", body_text="x\x00y")])).encode("utf-8")
    )
    r = await client.post(
        "/api/v1/ingest",
        headers={
            **bearer(users.token_a),
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
        content=payload,
    )
    assert r.status_code == 200, r.text
    with owner_engine.connect() as conn:
        assert tuple(conn.execute(text("select subject, body_text from messages")).one()) == (
            "ab",
            "xy",
        )


async def test_limits(engine_and_limits: dict[str, Any], users: SimpleNamespace) -> None:
    small = await make_client(
        engine_and_limits["engine"], max_body_bytes=2000, max_decompressed_bytes=4000
    )
    async with small:
        big = batch([mail(i, body_text="x" * 500) for i in range(5)])
        r = await small.post("/api/v1/ingest", headers=bearer(users.token_a), json=big)
        assert r.status_code == 413
        bomb = gzip.compress(json.dumps(batch([mail(1, body_text="y" * 10000)])).encode())
        assert len(bomb) < 2000
        r = await small.post(
            "/api/v1/ingest",
            headers={**bearer(users.token_a), "Content-Encoding": "gzip"},
            content=bomb,
        )
        assert r.status_code == 413
        r = await small.post(
            "/api/v1/ingest",
            headers={**bearer(users.token_a), "Content-Encoding": "gzip"},
            content=b"not gzip",
        )
        assert r.status_code == 400


async def test_too_many_messages_rejected(client: AsyncClient, users: SimpleNamespace) -> None:
    r = await client.post(
        "/api/v1/ingest", headers=bearer(users.token_a), json=batch([mail(i) for i in range(501)])
    )
    assert r.status_code == 422


# Collector có parser cũ (gửi nội dung file đính kèm text trong body_text) không được nạp thêm.
@pytest.mark.parametrize(
    "version", ["collector/0.3.0", "collector/0.2.9", "collector/test", "other/9.9.9"]
)
async def test_old_parser_version_rejected(
    client: AsyncClient, owner_engine: Engine, users: SimpleNamespace, version: str
) -> None:
    old = dict(batch([mail(1)]), parser_version=version)
    r = await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=old)
    assert r.status_code == 426
    assert counts(owner_engine, users.a) == (0, 0, 0)
    newer = dict(batch([mail(1)]), parser_version="collector/0.10.0")
    r = await client.post("/api/v1/ingest", headers=bearer(users.token_a), json=newer)
    assert r.status_code == 200


async def test_ingest_requires_token(client: AsyncClient, users: SimpleNamespace) -> None:
    r = await client.post("/api/v1/ingest", json=batch([mail(1)]))
    assert r.status_code == 401
