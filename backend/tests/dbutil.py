"""Tiện ích cho test: database test (mailassist_test), Alembic, tạo dữ liệu mẫu."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

from app.ai.glossary import normalize_term
from app.config import DbRole, database_url

TEST_DB = "mailassist_test"
BACKEND_DIR = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
PM = "pm@vn.example.com"
CLIENT = "client@a.example.jp"


def url_for_test_db(role: DbRole) -> str:
    """Giống database_url(role) nhưng trỏ tới database test."""
    return make_url(database_url(role)).set(database=TEST_DB).render_as_string(hide_password=False)


def run_alembic(cmd: Literal["upgrade", "downgrade"], rev: str) -> None:
    """Chạy migration trên database test bằng role owner."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url_for_test_db("owner").replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    if cmd == "upgrade":
        command.upgrade(cfg, rev)
    else:
        command.downgrade(cfg, rev)


def as_user(conn: Connection, user_id: int | str | None) -> None:
    """Đặt app.user_id cho transaction hiện tại (None: không đặt)."""
    if user_id is not None:
        conn.execute(text("select set_config('app.user_id', :v, true)"), {"v": str(user_id)})


# --- dữ liệu mẫu (chạy bằng kết nối owner, không qua RLS) ---


def add_user(conn: Connection, email: str, role: str = "member") -> int:
    return conn.scalar(
        text("insert into users (email, display_name, role) values (:e, :e, :r) returning id"),
        {"e": email, "r": role},
    )


def add_source(
    conn: Connection,
    user_id: int,
    folder_path: str = "ImapMail/imap.example.com/INBOX",
    kind: str = "imap_cache",
    account: str = "imap.example.com",
) -> int:
    return conn.scalar(
        text(
            "insert into mail_sources (user_id, kind, account, folder_path) "
            "values (:u, :k, :a, :p) returning id"
        ),
        {"u": user_id, "k": kind, "a": account, "p": folder_path},
    )


def add_message(conn: Connection, message_id_hash: str, *, user_id: int, **cols: Any) -> int:
    values = {
        "user_id": user_id,
        "message_id_hash": message_id_hash,
        "parser_version": "test",
        **cols,
    }
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    return conn.scalar(
        text(f"insert into messages ({names}) values ({params}) returning id"), values
    )


def add_mail(
    conn: Connection,
    user_id: int,
    mid: str,
    *,
    refs: tuple[str, ...] = (),
    irt: str | None = None,
    subject: str = "Báo giá",
    frm: str = CLIENT,
    to: tuple[str, ...] = (PM,),
    day: float | None = 0,
    body: str = "Nội dung.",
    cc: tuple[str, ...] = (),
) -> int:
    """Mail có header nối thread, địa chỉ và ngày (cho test làm sạch / gom thread)."""
    return conn.scalar(
        text(
            "insert into messages (user_id, message_id_hash, message_id, in_reply_to, "
            "references_ids, subject, from_addr, to_addrs, cc_addrs, sent_at, body_text, "
            "parser_version) "
            "values (:u, :h, :mid, :irt, :refs, :subject, :frm, :to, :cc, :at, :body, 'test') "
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
            "to": Jsonb([{"name": None, "addr": a} for a in to]),
            "cc": Jsonb([{"name": None, "addr": a} for a in cc]),
            "at": None if day is None else T0 + timedelta(days=day),
            "body": body,
        },
    )


def add_project(conn: Connection, user_id: int, name: str, **cols: Any) -> int:
    values = {"user_id": user_id, "name": name, **cols}
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    return conn.scalar(
        text(f"insert into projects ({names}) values ({params}) returning id"), values
    )


def add_glossary(
    conn: Connection,
    user_id: int,
    term: str,
    translation: str,
    *,
    project_id: int | None = None,
    note: str | None = None,
) -> int:
    """Thêm một mục glossary; term_norm tính đơn giản (NFKC, casefold, bỏ khoảng trắng)."""
    norm = normalize_term(term)
    return conn.scalar(
        text(
            "insert into glossary_terms (user_id, project_id, term, term_norm, translation, note) "
            "values (:u, :p, :t, :n, :tr, :note) returning id"
        ),
        {"u": user_id, "p": project_id, "t": term, "n": norm, "tr": translation, "note": note},
    )


def _insert_by_message(
    conn: Connection, table: str, user_id: int, message_id: int, values: dict[str, Any]
) -> None:
    values = {"user_id": user_id, "message_id": message_id, **values}
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"insert into {table} ({names}) values ({params})"), values)


def add_auto_run(
    conn: Connection, user_id: int, message_id: int, status: str = "done", **cols: Any
) -> None:
    _insert_by_message(
        conn, "message_auto_runs", user_id, message_id, {"status": status, "attempts": 1, **cols}
    )


def add_translation(conn: Connection, user_id: int, message_id: int, **cols: Any) -> None:
    values: dict[str, Any] = {
        "translated_subject": "Chủ đề",
        "translated_body": "Nội dung",
        "source_hash": b"\x00" * 32,
        "model": "test-model",
        "prompt_version": 1,
        **cols,
    }
    for k in ("notes", "warnings", "glossary_used"):
        if k in values:
            values[k] = Jsonb(values[k])
    _insert_by_message(conn, "message_translations", user_id, message_id, values)


def add_thread(conn: Connection, user_id: int, message_count: int = 1, **cols: Any) -> int:
    values = {"user_id": user_id, "message_count": message_count, "threader_version": 1, **cols}
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    return conn.scalar(
        text(f"insert into threads ({names}) values ({params}) returning id"), values
    )


def add_ownership(
    conn: Connection, message_id: int, user_id: int, source_id: int, mbox_offset: int = 0
) -> None:
    conn.execute(
        text(
            "insert into message_ownership (message_id, user_id, source_id, mbox_offset) "
            "values (:m, :u, :s, :o)"
        ),
        {"m": message_id, "u": user_id, "s": source_id, "o": mbox_offset},
    )


AI_TASKS = ("question_analysis", "answer", "translate")


def set_ai_models(conn: Connection, user_id: int, **models: str) -> None:
    """Tên model theo tác vụ của người dùng (spec 28); tác vụ không nêu dùng "test-model"."""
    for task, model in {**dict.fromkeys(AI_TASKS, "test-model"), **models}.items():
        conn.execute(
            text(
                "insert into user_ai_settings (user_id, task, model) values (:u, :t, :m) "
                "on conflict (user_id, task) do update set model = excluded.model"
            ),
            {"u": user_id, "t": task, "m": model},
        )
