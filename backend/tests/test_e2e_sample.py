"""Tích hợp: mail của profile tự sinh → DB test → process_user (làm sạch + gom) → so đáp án.

Profile sinh vào thư mục tạm bằng data/generate_sample_profile.py (fixture sample_profile của
test_threading), không dùng data/thunderbird-profile.
"""

import mailbox
from email.message import Message
from pathlib import Path

from psycopg.types.json import Jsonb
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.mail.hashing import normalize_message_id
from app.processing import pending_users, process_user
from tests.dbutil import add_user
from tests.test_threading import load_rows, sample_profile  # noqa: F401 (fixture)


def _body(msg: Message) -> str:
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html") and not part.get_filename():
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def load_bodies(profile: Path) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for path in sorted(profile.rglob("*")):
        if path.is_file() and not path.suffix and path.stat().st_size:
            for msg in mailbox.mbox(path, create=False):
                mid = normalize_message_id(msg["Message-ID"]) or ""
                bodies.setdefault(mid, _body(msg))
    return bodies


async def test_sample_profile_through_db(
    sample_profile: tuple[Path, dict[str, list[str]]],  # noqa: F811
    owner_engine: Engine,
    worker: AsyncEngine,
    clean: None,
) -> None:
    profile, expected = sample_profile
    rows = load_rows(profile)
    bodies = load_bodies(profile)
    with owner_engine.begin() as conn:
        user = add_user(conn, "pm@vn-outsource.example.com")
        conn.execute(
            text(
                "insert into messages (user_id, message_id_hash, message_id, in_reply_to, "
                "references_ids, thread_index, subject, from_addr, to_addrs, sent_at, "
                "body_text, parser_version) values (:u, :h, :mid, :irt, :refs, :ti, :subject, "
                ":frm, :to, :at, :body, 'test')"
            ),
            [
                {
                    "u": user,
                    "h": f"h-{r.message_id}",
                    "mid": r.message_id,
                    "irt": r.in_reply_to,
                    "refs": list(r.references),
                    "ti": r.thread_index,
                    "subject": r.subject,
                    "frm": r.from_addr,
                    "to": Jsonb([{"name": None, "addr": a} for a in sorted(r.recipients)]),
                    "at": r.sent_at,
                    "body": bodies[r.message_id or ""],
                }
                for r in rows
            ],
        )

    result = await process_user(worker, user)
    assert result.clean.failed == 0
    assert result.threads is not None
    assert (result.threads.messages, result.threads.threads) == (len(rows), len(expected))
    assert await pending_users(worker) == []

    with owner_engine.connect() as conn:
        db_groups: dict[int, set[str]] = {}
        for r in conn.execute(
            text("select thread_id, message_id from messages where user_id = :u"), {"u": user}
        ):
            db_groups.setdefault(r.thread_id, set()).add(r.message_id)
        stats = conn.execute(
            text(
                'select count(*) filter (where clean_cuts @> \'[{"kind": "quote"}]\') q, '
                'count(*) filter (where clean_cuts @> \'[{"kind": "signature"}]\') s, '
                "count(*) filter (where body_clean like '%> %') leftover_quote, "
                "count(*) filter (where subject like 'Fwd:%' "
                "and body_clean not like '%Forwarded message%') fwd_cut "
                "from messages where user_id = :u"
            ),
            {"u": user},
        ).one()
    want = {frozenset(normalize_message_id(m) for m in mids) for mids in expected.values()}
    assert {frozenset(g) for g in db_groups.values()} == want
    # Mail trả lời có quote bị cắt; không còn dòng quote trong body_clean; nội dung chuyển tiếp
    # được giữ; chữ ký "--" bị cắt ở phần lớn mail.
    assert stats.q > len(rows) // 3
    assert stats.s > len(rows) // 2
    assert (stats.leftover_quote, stats.fwd_cut) == (0, 0)
