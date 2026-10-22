import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Base
from tests.conftest import MAIL_TABLES, truncate_mail_tables
from tests.dbutil import add_message, add_ownership, add_source, add_user, run_alembic

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
