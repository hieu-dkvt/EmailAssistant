"""API của web: search, danh sách/chi tiết thread, toàn văn, sửa tay, trạng thái (spec 14, 15).

Dữ liệu tự dựng (tên miền *.example.*). Hai người dùng A, B; B có bản riêng của cùng một mail
(cùng Message-ID) với A: mọi endpoint chỉ cho mỗi người thấy bản của mình.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import psycopg.errors
import pytest
from httpx import AsyncClient
from psycopg.types.json import Jsonb
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import threads_view
from app.db.session import create_api_engine
from app.processing import process_user
from app.search import keyword
from tests.dbutil import T0, add_ownership, add_source, as_user, url_for_test_db
from tests.webutil import CSRF, add_web_user, login, web_client

PM = "pm@vn-outsource.example.com"
TANAKA = "tanaka@client-a.example.jp"
SUBJECT = "【サンプルA】お見積りのお願い"
QUOTED_REPLY = (
    "田中様\n\n承知しました。\n\n"
    "2026年9月1日(火) 10:00 田中 健二 <tanaka@client-a.example.jp>:\n"
    "> 在庫管理システムの見積をお願いします。\n\n"
    "-- \nTrần Hiếu\nVN Outsource\n"
)


def put_mail(
    conn: Connection,
    user_id: int,
    mid: str,
    folders: list[int],
    *,
    subject: str,
    body: str,
    frm: str = TANAKA,
    from_name: str | None = None,
    to: tuple[str, ...] = (PM,),
    cc: tuple[str, ...] = (),
    day: float | None = 0,
    irt: str | None = None,
    refs: tuple[str, ...] = (),
    attachments: list[dict[str, Any]] | None = None,
) -> int:
    m: int = conn.scalar(
        text(
            "insert into messages (user_id, message_id_hash, message_id, in_reply_to, "
            "references_ids, subject, from_addr, from_name, to_addrs, cc_addrs, sent_at, "
            "body_text, has_attachments, attachments, parser_version) values (:u, :h, :mid, :irt, "
            ":refs, :subject, :frm, :fname, :to, :cc, :at, :body, :has_att, :att, 'test') "
            "returning id"
        ),
        {
            "u": user_id,
            "h": f"h-{mid}",
            "mid": mid,
            "irt": irt,
            "refs": list(refs),
            "subject": subject,
            "frm": frm,
            "fname": from_name,
            "to": Jsonb([{"name": None, "addr": a} for a in to]),
            "cc": Jsonb([{"name": None, "addr": a} for a in cc]),
            "at": None if day is None else T0 + timedelta(days=day),
            "body": body,
            "has_att": bool(attachments),
            "att": Jsonb(attachments or []),
        },
    )
    for i, s in enumerate(folders):
        add_ownership(conn, m, user_id, s, mbox_offset=m * 10 + i)
    return m


@pytest.fixture
async def api() -> AsyncIterator[AsyncEngine]:
    engine = create_api_engine(url_for_test_db("api"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def client_a(api: AsyncEngine, box: SimpleNamespace) -> AsyncIterator[AsyncClient]:
    async with web_client(api) as c:
        await login(c, "a@example.com")
        yield c


@pytest.fixture
async def client_b(api: AsyncEngine, box: SimpleNamespace) -> AsyncIterator[AsyncClient]:
    async with web_client(api) as c:
        await login(c, "b@example.com")
        yield c


@pytest.fixture
async def box(owner_engine: Engine, worker: AsyncEngine, clean: None) -> SimpleNamespace:
    a = add_web_user(owner_engine, "a@example.com")
    b = add_web_user(owner_engine, "b@example.com")
    with owner_engine.begin() as conn:
        inbox = add_source(conn, a, "ImapMail/imap.example.com/INBOX")
        proj = add_source(conn, a, "ImapMail/imap.example.com/INBOX.sbd/ProjectA")
        inbox_b = add_source(conn, b, "ImapMail/imap.example.com/INBOX")
        a1 = put_mail(
            conn,
            a,
            "est-1@a.example.jp",
            [inbox, proj],
            subject=SUBJECT,
            body="お世話になっております。\n在庫管理システムの見積をお願いします。",
            from_name="田中 健二",
            cc=("sato@client-a.example.jp",),
        )
        a2 = put_mail(
            conn,
            a,
            "est-2@vn.example.com",
            [proj],
            subject="Re: " + SUBJECT,
            body=QUOTED_REPLY,
            frm=PM,
            from_name="Trần Hiếu",
            to=(TANAKA,),
            day=1,
            irt="est-1@a.example.jp",
            refs=("est-1@a.example.jp",),
        )
        a3 = put_mail(
            conn,
            a,
            "est-3@a.example.jp",
            [proj],
            subject="Re: " + SUBJECT,
            body="見積書を送付します。工数は別紙の通りです。",
            from_name="田中 健二",
            day=2,
            irt="est-2@vn.example.com",
            refs=("est-1@a.example.jp", "est-2@vn.example.com"),
            attachments=[{"filename": "estimate.xlsx", "size": 12345, "content_type": "x/y"}],
        )
        a4 = put_mail(
            conn,
            a,
            "inv-1@b.example.jp",
            [inbox],
            subject="9月分請求書の送付について",
            body="請求書をお送りします。Invoice PRJ-1234",
            frm="keiri@client-b.example.jp",
            day=10,
        )
        a5 = put_mail(
            conn,
            a,
            "inv-2@vn.example.com",
            [inbox],
            subject="Re: 9月分請求書の送付について",
            body="受領しました。",
            frm=PM,
            to=("keiri@client-b.example.jp",),
            day=11,
            irt="inv-1@b.example.jp",
            refs=("inv-1@b.example.jp",),
        )
        a6 = put_mail(
            conn, a, "nodate@z.example.com", [inbox], subject="日付なし", body="本文", day=None
        )
        b1 = put_mail(
            conn, b, "est-1@a.example.jp", [inbox_b], subject=SUBJECT, body="B側の見積本文"
        )
    for u in (a, b):
        await process_user(worker, u)
    with owner_engine.connect() as conn:
        threads = {
            r.id: r.thread_id for r in conn.execute(text("select id, thread_id from messages"))
        }
    return SimpleNamespace(
        a=a,
        b=b,
        inbox=inbox,
        proj=proj,
        inbox_b=inbox_b,
        a1=a1,
        a2=a2,
        a3=a3,
        a4=a4,
        a5=a5,
        a6=a6,
        b1=b1,
        t1=threads[a1],
        t2=threads[a4],
        t3=threads[a6],
        tb=threads[b1],
    )


def ids(r: Any) -> list[int]:
    return [i["message_id"] for i in r.json()["items"]]


def hit_texts(segments: list[dict[str, Any]]) -> list[str]:
    return [s["text"] for s in segments if s["hit"]]


# --- search ---


async def test_search_finds_and_highlights(client_a: AsyncClient, box: SimpleNamespace) -> None:
    r = await client_a.get("/api/v1/search", params={"q": "見積"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3 and body["keywords"] == ["見積"]
    assert ids(r) == [box.a3, box.a2, box.a1]  # mới nhất trước
    first = {i["message_id"]: i for i in body["items"]}
    assert hit_texts(first[box.a1]["subject"]) == ["見積"]
    assert hit_texts(first[box.a1]["snippet"]) == ["見積"]
    assert first[box.a3]["has_attachments"] is True
    assert first[box.a3]["thread_id"] == box.t1 and first[box.a3]["thread_message_count"] == 3
    assert [f["path"] for f in first[box.a1]["folders"]] == [
        "ImapMail/imap.example.com/INBOX",
        "ImapMail/imap.example.com/INBOX.sbd/ProjectA",
    ]
    # Quote đã cắt khỏi body_clean: mail trả lời chỉ khớp nhờ subject.
    assert hit_texts(first[box.a2]["snippet"]) == []


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"q": "見積 -工数"}, ["a2", "a1"]),
        ({"q": "見積 OR 請求書"}, ["a5", "a4", "a3", "a2", "a1"]),
        ({"q": "invoice"}, ["a4"]),
        ({"q": "prj"}, ["a4"]),
        ({"q": '"PRJ-1234"'}, ["a4"]),
        ({"sender": "TANAKA@"}, ["a3", "a1", "a6"]),
        ({"sender": "田中"}, ["a3", "a1"]),
        ({"sender": "%"}, []),
        ({"q": "見積", "folder_id": "inbox"}, ["a1"]),
        ({"q": "見積", "has_attachments": "true"}, ["a3"]),
        ({"has_attachments": "false", "folder_id": "proj"}, ["a2", "a1"]),
        ({"sent_after": "day1", "sent_before": "day10"}, ["a3", "a2"]),
    ],
)
async def test_search_filters(
    client_a: AsyncClient, box: SimpleNamespace, params: dict[str, str], expected: list[str]
) -> None:
    resolved = dict(params)
    if "folder_id" in resolved:
        resolved["folder_id"] = str(getattr(box, resolved["folder_id"]))
    for key in ("sent_after", "sent_before"):
        if key in resolved:
            resolved[key] = (T0 + timedelta(days=int(resolved[key][3:]))).isoformat()
    r = await client_a.get("/api/v1/search", params=resolved)
    assert r.status_code == 200, r.text
    assert ids(r) == [getattr(box, k) for k in expected]
    assert r.json()["total"] == len(expected)


async def test_search_sort_and_paging(client_a: AsyncClient, box: SimpleNamespace) -> None:
    r = await client_a.get("/api/v1/search", params={"q": "見積", "sort": "relevance"})
    assert sorted(ids(r)) == sorted([box.a1, box.a2, box.a3])
    pages = []
    for offset in (0, 2):
        r = await client_a.get("/api/v1/search", params={"q": "見積", "limit": 2, "offset": offset})
        assert r.json()["total"] == 3
        pages += ids(r)
    assert pages == [box.a3, box.a2, box.a1]
    # Không có q: chỉ lọc, sắp theo ngày, mail không có ngày ở cuối.
    r = await client_a.get("/api/v1/search")
    assert ids(r) == [box.a5, box.a4, box.a3, box.a2, box.a1, box.a6]


async def test_search_total_is_capped(
    client_a: AsyncClient, box: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keyword, "TOTAL_CAP", 2)
    r = await client_a.get("/api/v1/search", params={"q": "見積"})
    body = r.json()
    assert (body["total"], body["total_capped"]) == (2, True)
    assert ids(r) == [box.a3, box.a2, box.a1]  # trang vẫn đủ, chỉ số đếm bị giới hạn
    r = await client_a.get("/api/v1/search", params={"q": "請求書"})
    assert (r.json()["total"], r.json()["total_capped"]) == (2, False)


async def test_search_is_per_user(
    client_a: AsyncClient, client_b: AsyncClient, box: SimpleNamespace
) -> None:
    r = await client_b.get("/api/v1/search", params={"q": "見積"})
    assert ids(r) == [box.b1]
    assert hit_texts(r.json()["items"][0]["snippet"]) == ["見積"]
    assert box.b1 not in ids(await client_a.get("/api/v1/search", params={"q": "B側"}))
    # Folder của người khác: không có kết quả.
    r = await client_b.get("/api/v1/search", params={"folder_id": box.inbox})
    assert r.json()["total"] == 0
    r = await client_b.get("/api/v1/search", params={"q": "請求書"})
    assert r.json()["total"] == 0


@pytest.mark.parametrize(
    ("q", "code"), [("-工数", "query_needs_positive_term"), ("x" * 501, "query_too_long")]
)
async def test_search_bad_query(client_a: AsyncClient, q: str, code: str) -> None:
    r = await client_a.get("/api/v1/search", params={"q": q})
    assert r.status_code == 400 and r.json()["code"] == code


async def test_web_endpoints_require_session(api: AsyncEngine, box: SimpleNamespace) -> None:
    async with web_client(api) as c:
        for path in (
            "/api/v1/search",
            "/api/v1/folders",
            "/api/v1/threads",
            f"/api/v1/threads/{box.t1}",
            f"/api/v1/messages/{box.a1}",
            f"/api/v1/messages/{box.a1}/full",
            "/api/v1/processing",
            "/api/v1/thread-overrides",
        ):
            r = await c.get(path)
            assert r.status_code == 401, path
            assert r.json()["code"] == "not_authenticated"


def test_search_uses_index_under_rls(api_engine: Engine, box: SimpleNamespace) -> None:
    with api_engine.begin() as conn:
        as_user(conn, box.a)
        conn.execute(text("set local enable_seqscan = off"))
        plan = "\n".join(
            conn.scalars(
                text(
                    "explain select m.id from messages m "
                    "where ARRAY[m.subject, m.body_clean] &@~ :q "
                    "order by m.sent_at desc nulls last, m.id desc limit 50"
                ),
                {"q": '"見積"'},
            )
        )
    assert "ix_messages_search" in plan


# --- danh sách / chi tiết thread ---


async def test_folders(client_a: AsyncClient, client_b: AsyncClient, box: SimpleNamespace) -> None:
    r = await client_a.get("/api/v1/folders")
    assert [(f["id"], f["message_count"]) for f in r.json()] == [(box.inbox, 4), (box.proj, 3)]
    r = await client_b.get("/api/v1/folders")
    assert [f["id"] for f in r.json()] == [box.inbox_b]


async def test_thread_list_order_and_cursor(client_a: AsyncClient, box: SimpleNamespace) -> None:
    r = await client_a.get("/api/v1/threads")
    page = r.json()
    assert [t["id"] for t in page["items"]] == [box.t2, box.t1, box.t3]
    assert page["next_cursor"] is None
    t1 = page["items"][1]
    assert t1["subject"] == SUBJECT and t1["message_count"] == 3
    assert t1["last_from_addr"] == TANAKA and t1["has_attachments"] is True
    seen: list[int] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = (await client_a.get("/api/v1/threads", params=params)).json()
        seen += [t["id"] for t in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [box.t2, box.t1, box.t3]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"has_attachments": "true"}, ["t1"]),
        ({"sender": "keiri"}, ["t2"]),
        ({"folder_id": "proj"}, ["t1"]),
        ({"sent_before": "day5"}, ["t1"]),
        ({"sender": "tanaka", "has_attachments": "false"}, ["t1", "t3"]),
    ],
)
async def test_thread_list_filters(
    client_a: AsyncClient, box: SimpleNamespace, params: dict[str, str], expected: list[str]
) -> None:
    resolved = dict(params)
    if "folder_id" in resolved:
        resolved["folder_id"] = str(getattr(box, resolved["folder_id"]))
    if "sent_before" in resolved:
        resolved["sent_before"] = (T0 + timedelta(days=5)).isoformat()
    r = await client_a.get("/api/v1/threads", params=resolved)
    assert [t["id"] for t in r.json()["items"]] == [getattr(box, k) for k in expected]


async def test_thread_list_bad_cursor(client_a: AsyncClient) -> None:
    for cursor in ("x", "WzEsMl0", "bnVsbA"):  # rác, [1,2], null
        r = await client_a.get("/api/v1/threads", params={"cursor": cursor})
        assert r.status_code == 400 and r.json()["code"] == "bad_cursor", cursor


async def test_thread_list_is_per_user(client_b: AsyncClient, box: SimpleNamespace) -> None:
    r = await client_b.get("/api/v1/threads")
    assert [t["id"] for t in r.json()["items"]] == [box.tb]


async def test_thread_detail(client_a: AsyncClient, box: SimpleNamespace) -> None:
    r = await client_a.get(f"/api/v1/threads/{box.t1}", params={"q": "見積"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert (t["subject"], t["message_count"], t["truncated"]) == (SUBJECT, 3, False)
    assert t["keywords"] == ["見積"]
    m1, m2, m3 = t["messages"]
    assert [m1["id"], m2["id"], m3["id"]] == [box.a1, box.a2, box.a3]
    assert m1["thread_method"] == "root" and m2["thread_parent_id"] == box.a1
    assert hit_texts(m1["subject"]) == ["見積"] and hit_texts(m1["body"]) == ["見積"]
    assert m1["cc"] == [{"name": None, "addr": "sato@client-a.example.jp"}]
    assert len(m1["folders"]) == 2
    # Mail trả lời: quote và chữ ký đã cắt, body chỉ còn phần mới.
    assert m2["cleaned"] is True and m2["cut_count"] == 2 and m2["body_truncated"] is False
    body2 = "".join(s["text"] for s in m2["body"])
    assert "承知しました" in body2 and "> " not in body2 and "VN Outsource" not in body2
    assert m3["attachments"] == [
        {"filename": "estimate.xlsx", "size": 12345, "content_type": "x/y"}
    ]
    # Không có q: không tô gì.
    r = await client_a.get(f"/api/v1/threads/{box.t1}")
    assert all(not s["hit"] for m in r.json()["messages"] for s in m["body"])


async def test_thread_body_budget(
    client_a: AsyncClient, box: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mỗi mail tối đa BODY_LIMIT ký tự, cả thread tối đa THREAD_BODY_BUDGET: mail sau nhận phần còn
    # lại, phần bị cắt xem ở "Toàn văn".
    monkeypatch.setattr(threads_view, "BODY_LIMIT", 10)
    monkeypatch.setattr(threads_view, "THREAD_BODY_BUDGET", 15)
    r = await client_a.get(f"/api/v1/threads/{box.t1}", params={"q": "見積"})
    msgs = r.json()["messages"]
    assert [len("".join(s["text"] for s in m["body"])) for m in msgs] == [10, 5, 0]
    assert [m["body_truncated"] for m in msgs] == [True, True, True]


async def test_thread_detail_not_visible(
    client_a: AsyncClient, client_b: AsyncClient, box: SimpleNamespace
) -> None:
    for c, tid in ((client_b, box.t1), (client_a, box.tb), (client_a, 10**12)):
        r = await c.get(f"/api/v1/threads/{tid}")
        assert r.status_code == 404 and r.json()["code"] == "thread_not_found"
    assert (await client_a.get(f"/api/v1/threads/{2**63}")).status_code == 422


async def test_message_full_and_ref(
    client_a: AsyncClient, client_b: AsyncClient, box: SimpleNamespace
) -> None:
    r = await client_a.get(f"/api/v1/messages/{box.a2}/full", params={"q": "見積"})
    assert r.status_code == 200, r.text
    parts = r.json()["parts"]
    assert [p["kind"] for p in parts] == ["body", "quote", "body", "signature"]
    assert "".join(s["text"] for p in parts for s in p["segments"]) == QUOTED_REPLY
    assert hit_texts(parts[1]["segments"]) == ["見積"]
    assert (await client_a.get(f"/api/v1/messages/{box.a2}")).json() == {
        "id": box.a2,
        "thread_id": box.t1,
    }
    for path in (f"/api/v1/messages/{box.a2}", f"/api/v1/messages/{box.a2}/full"):
        r = await client_b.get(path)
        assert r.status_code == 404 and r.json()["code"] == "message_not_found"


# --- sửa tay ---


async def state(owner_engine: Engine, user_id: int) -> tuple[int, int]:
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "select overrides_version, applied_overrides_version from user_thread_state "
                "where user_id = :u"
            ),
            {"u": user_id},
        ).first()
    return (row[0], row[1]) if row else (0, 0)


async def thread_of(client: AsyncClient, message_id: int) -> int:
    return (await client.get(f"/api/v1/messages/{message_id}")).json()["thread_id"]


async def test_split_merge_undo(
    client_a: AsyncClient, owner_engine: Engine, worker: AsyncEngine, box: SimpleNamespace
) -> None:
    r = await client_a.post(
        "/api/v1/thread-overrides", json={"action": "split", "message_id": box.a3}, headers=CSRF
    )
    assert r.status_code == 201, r.text
    split = r.json()
    assert split["action"] == "split" and split["message"]["id"] == box.a3
    assert split["target"] is None
    assert (await client_a.get("/api/v1/processing")).json() == {
        "rethread_pending": True,
        "unprocessed_messages": 0,
    }
    assert await state(owner_engine, box.a) == (1, 0)
    # Gửi lại cùng sửa tay: trả bản đã có, không tăng version.
    r = await client_a.post(
        "/api/v1/thread-overrides", json={"action": "split", "message_id": box.a3}, headers=CSRF
    )
    assert r.status_code == 200 and r.json()["id"] == split["id"]
    assert await state(owner_engine, box.a) == (1, 0)

    await process_user(worker, box.a)
    assert await state(owner_engine, box.a) == (1, 1)
    assert (await client_a.get("/api/v1/processing")).json()["rethread_pending"] is False
    assert await thread_of(client_a, box.a3) != await thread_of(client_a, box.a1)
    assert await thread_of(client_a, box.a2) == box.t1

    r = await client_a.post(
        "/api/v1/thread-overrides",
        json={"action": "merge", "message_id": box.a4, "target_message_id": box.a1},
        headers=CSRF,
    )
    assert r.status_code == 201, r.text
    assert r.json()["target"]["id"] == box.a1
    await process_user(worker, box.a)
    assert await thread_of(client_a, box.a4) == await thread_of(client_a, box.a1)
    assert await thread_of(client_a, box.a5) == await thread_of(client_a, box.a1)

    listed = (await client_a.get("/api/v1/thread-overrides")).json()
    assert [(o["action"], o["message"]["id"]) for o in listed] == [
        ("merge", box.a4),
        ("split", box.a3),
    ]
    r = await client_a.delete(f"/api/v1/thread-overrides/{split['id']}", headers=CSRF)
    assert r.status_code == 204
    assert await state(owner_engine, box.a) == (3, 2)
    await process_user(worker, box.a)
    assert await thread_of(client_a, box.a3) == await thread_of(client_a, box.a1)
    r = await client_a.delete(f"/api/v1/thread-overrides/{split['id']}", headers=CSRF)
    assert r.status_code == 404 and r.json()["code"] == "override_not_found"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"action": "split", "message_id": "a1"}, 400, "cannot_split_root"),
        ({"action": "merge", "message_id": "a3", "target_message_id": "a1"}, 400, "same_thread"),
        ({"action": "split", "message_id": "b1"}, 404, "message_not_found"),
        (
            {"action": "merge", "message_id": "a4", "target_message_id": "b1"},
            404,
            "message_not_found",
        ),
        ({"action": "split", "message_id": 10**12}, 404, "message_not_found"),
    ],
)
async def test_override_errors(
    client_a: AsyncClient, box: SimpleNamespace, body: dict[str, Any], status: int, code: str
) -> None:
    resolved = {
        k: getattr(box, v) if isinstance(v, str) and k != "action" else v for k, v in body.items()
    }
    r = await client_a.post("/api/v1/thread-overrides", json=resolved, headers=CSRF)
    assert (r.status_code, r.json().get("code")) == (status, code)


async def test_override_validation_and_csrf(client_a: AsyncClient, box: SimpleNamespace) -> None:
    for body in (
        {"action": "merge", "message_id": box.a4},
        {"action": "split", "message_id": box.a3, "target_message_id": box.a1},
        {"action": "merge", "message_id": box.a4, "target_message_id": box.a4},
        {"action": "join", "message_id": box.a4},
    ):
        r = await client_a.post("/api/v1/thread-overrides", json=body, headers=CSRF)
        assert r.status_code == 422, body
    r = await client_a.post(
        "/api/v1/thread-overrides", json={"action": "split", "message_id": box.a3}
    )
    assert r.status_code == 403 and r.json()["code"] == "csrf_header_missing"


async def test_override_not_threaded(
    client_a: AsyncClient, owner_engine: Engine, box: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        late = put_mail(conn, box.a, "late@a.example.jp", [box.inbox], subject="mới", body="x")
    r = await client_a.post(
        "/api/v1/thread-overrides", json={"action": "split", "message_id": late}, headers=CSRF
    )
    assert r.status_code == 409 and r.json()["code"] == "not_threaded"
    assert (await client_a.get("/api/v1/processing")).json()["unprocessed_messages"] == 1


async def test_overrides_are_per_user(
    client_a: AsyncClient, client_b: AsyncClient, box: SimpleNamespace
) -> None:
    r = await client_a.post(
        "/api/v1/thread-overrides", json={"action": "split", "message_id": box.a3}, headers=CSRF
    )
    override_id = r.json()["id"]
    assert (await client_b.get("/api/v1/thread-overrides")).json() == []
    r = await client_b.delete(f"/api/v1/thread-overrides/{override_id}", headers=CSRF)
    assert r.status_code == 404
    assert len((await client_a.get("/api/v1/thread-overrides")).json()) == 1
    assert (await client_b.get("/api/v1/processing")).json()["rethread_pending"] is False


def test_api_role_override_privileges(api_engine: Engine, box: SimpleNamespace) -> None:
    # Ghi override cho người khác: RLS WITH CHECK chặn.
    with pytest.raises(DBAPIError) as ei, api_engine.begin() as conn:
        as_user(conn, box.a)
        conn.execute(
            text(
                "insert into thread_overrides (user_id, action, message_id) "
                "values (:u, 'split', :m)"
            ),
            {"u": box.b, "m": box.b1},
        )
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    # Mail của người khác với user_id của mình: FK kép chặn.
    with pytest.raises(DBAPIError) as ei, api_engine.begin() as conn:
        as_user(conn, box.a)
        conn.execute(
            text(
                "insert into thread_overrides (user_id, action, message_id) "
                "values (:u, 'split', :m)"
            ),
            {"u": box.a, "m": box.b1},
        )
    assert isinstance(ei.value.orig, psycopg.errors.ForeignKeyViolation)
    for sql in (
        "update thread_overrides set message_id = message_id",
        "update user_thread_state set applied_overrides_version = 0",
        "update threads set message_count = 0",
        "update messages set thread_id = null",
    ):
        with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
            as_user(conn, box.a)
            conn.execute(text(sql))
        assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege), sql
