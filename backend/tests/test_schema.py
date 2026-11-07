import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Base
from tests.conftest import MAIL_TABLES, truncate_mail_tables
from tests.dbutil import (
    add_message,
    add_ownership,
    add_source,
    add_user,
    as_user,
    run_alembic,
)

UNICODE_TEXT = "【至急】①㈱Ⅲ お見積り – Báo giá dự án 🚀"


def constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def test_tables_exist(owner_engine: Engine, migrated: None) -> None:
    assert set(MAIL_TABLES) <= set(inspect(owner_engine).get_table_names())


def test_models_match_migration(owner_engine: Engine, migrated: None) -> None:
    with owner_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_duplicate_hash_rejected_per_user(owner_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        add_message(conn, "h1", user_id=a)
        add_message(conn, "h1", user_id=b)  # cùng mail, người khác: bản riêng, hợp lệ
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        add_message(conn, "h1", user_id=a)
    assert constraint_name(ei.value) == "uq_messages_user_id_message_id_hash"


def test_ownership_source_must_belong_to_user(owner_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        source_b = add_source(conn, b)
        m = add_message(conn, "h1", user_id=a)
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        add_ownership(conn, m, a, source_b)
    assert constraint_name(ei.value) == "fk_message_ownership_source_user"


def test_ownership_message_must_belong_to_user(owner_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        source_a = add_source(conn, a)
        m_b = add_message(conn, "h1", user_id=b)
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        add_ownership(conn, m_b, a, source_a)
    assert constraint_name(ei.value) == "fk_message_ownership_message_user"


@pytest.mark.parametrize(
    ("email", "role", "constraint"),
    [
        ("x@example.com", "boss", "ck_users_role"),
        ("X@Example.com", "member", "ck_users_email_lower"),
    ],
)
def test_user_check_constraints(
    owner_engine: Engine, clean: None, email: str, role: str, constraint: str
) -> None:
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        add_user(conn, email, role=role)
    assert constraint_name(ei.value) == constraint


def test_source_kind_check(owner_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        u = add_user(conn, "a@example.com")
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        add_source(conn, u, kind="pop")
    assert constraint_name(ei.value) == "ck_mail_sources_kind"


def test_defaults(owner_engine: Engine, clean: None) -> None:
    with owner_engine.begin() as conn:
        m = add_message(conn, "h1", user_id=add_user(conn, "a@example.com"))
        row = conn.execute(
            text(
                "select to_addrs, cc_addrs, references_ids, attachments, body_text, "
                "has_attachments, body_from_html from messages where id = :id"
            ),
            {"id": m},
        ).one()
    assert tuple(row) == ([], [], [], [], "", False, False)


def test_unicode_roundtrip(owner_engine: Engine, clean: None) -> None:
    body = UNICODE_TEXT + "\n\nTiếng Việt có dấu"
    with owner_engine.begin() as conn:
        assert conn.scalar(text("show server_encoding")) == "UTF8"
        u = add_user(conn, "a@example.com")
        m = add_message(conn, "h1", user_id=u, subject=UNICODE_TEXT, body_text=body)
    with owner_engine.connect() as conn:
        row = conn.execute(
            text("select subject, body_text from messages where id = :id"), {"id": m}
        ).one()
    assert tuple(row) == (UNICODE_TEXT, body)


def test_0004_requires_empty_messages(owner_engine: Engine, clean: None) -> None:
    # Mail dùng chung (schema cũ) không suy ra được thuộc ai: dừng với thông báo rõ ràng.
    run_alembic("downgrade", "0003")
    try:
        with owner_engine.begin() as conn:
            conn.execute(
                text("insert into messages (message_id_hash, parser_version) values ('h', 't')")
            )
        with pytest.raises(RuntimeError, match="chỉ chạy trên bảng rỗng"):
            run_alembic("upgrade", "0004")
    finally:
        truncate_mail_tables(owner_engine)
        run_alembic("upgrade", "head")


def test_downgrade_upgrade_roundtrip(owner_engine: Engine, clean: None) -> None:
    run_alembic("downgrade", "base")
    assert not set(MAIL_TABLES) & set(inspect(owner_engine).get_table_names())
    run_alembic("upgrade", "head")
    assert set(MAIL_TABLES) <= set(inspect(owner_engine).get_table_names())


def test_migration_drops_tier(owner_engine: Engine, api_engine: Engine, clean: None) -> None:
    columns = "select column_name, is_nullable from information_schema.columns "
    columns += "where table_name = 'user_ai_settings'"
    run_alembic("downgrade", "0018")
    try:
        with owner_engine.begin() as conn:
            a = add_user(conn, "a@example.com")
            b = add_user(conn, "b@example.com")
            for user, task, tier, model in (
                (a, "answer", "sonnet", None),  # chỉ có cấp: bị xóa
                (a, "translate", None, "gw/model-a"),
                (b, "translate", "haiku", "gw/model-b"),
            ):
                conn.execute(
                    text(
                        "insert into user_ai_settings (user_id, task, tier, model) "
                        "values (:u, :t, :tier, :m)"
                    ),
                    {"u": user, "t": task, "tier": tier, "m": model},
                )
        run_alembic("upgrade", "head")
        with owner_engine.connect() as conn:
            cols = {r.column_name: r.is_nullable for r in conn.execute(text(columns))}
            rows = conn.execute(
                text("select user_id, task, model from user_ai_settings order by user_id")
            ).all()
        assert "tier" not in cols and cols["model"] == "NO"
        assert [tuple(r) for r in rows] == [
            (a, "translate", "gw/model-a"),
            (b, "translate", "gw/model-b"),
        ]
        # app_api xóa được dòng của mình, không xóa được dòng của người khác (RLS).
        with api_engine.begin() as conn:
            as_user(conn, a)
            assert conn.execute(text("delete from user_ai_settings")).rowcount == 1
        with owner_engine.connect() as conn:
            left = conn.execute(text("select user_id from user_ai_settings")).all()
        assert [r.user_id for r in left] == [b]
    finally:
        truncate_mail_tables(owner_engine)
        run_alembic("upgrade", "head")


def test_migration_keeps_only_openai_protocol(owner_engine: Engine, clean: None) -> None:
    insert = text(
        "insert into user_ai_provider (user_id, protocol, base_url, api_key_enc) "
        "values (:u, :p, :url, 'enc')"
    )
    run_alembic("downgrade", "0020")
    try:
        with owner_engine.begin() as conn:
            a = add_user(conn, "a@example.com")
            b = add_user(conn, "b@example.com")
            conn.execute(insert, {"u": a, "p": "anthropic", "url": "http://10.0.0.5:20128"})
            conn.execute(insert, {"u": b, "p": "openai", "url": "http://10.0.0.6:20128/v1"})
        run_alembic("upgrade", "head")
        with owner_engine.connect() as conn:
            rows = conn.execute(
                text("select user_id, protocol, base_url, api_key_enc from user_ai_provider")
            ).all()
        # Dòng cũ đổi giao thức, giữ URL và key đã mã hóa.
        assert sorted(tuple(r) for r in rows) == [
            (a, "openai", "http://10.0.0.5:20128", "enc"),
            (b, "openai", "http://10.0.0.6:20128/v1", "enc"),
        ]
        with pytest.raises(IntegrityError), owner_engine.begin() as conn:
            conn.execute(text("update user_ai_provider set protocol = 'anthropic'"))
    finally:
        truncate_mail_tables(owner_engine)
        run_alembic("upgrade", "head")
