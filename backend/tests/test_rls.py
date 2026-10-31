"""Row-Level Security: người dùng chỉ thấy và chỉ ghi được dữ liệu mail của chính mình."""

from types import SimpleNamespace

import psycopg.errors
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.conftest import MAIL_TABLES
from tests.dbutil import add_message, add_ownership, add_source, as_user

type Visible = tuple[set[int], set[tuple[int, int]], set[int]]


def visible(engine: Engine, user_id: int | str | None) -> Visible:
    """(messages.id, (message_ownership.message_id, source_id), mail_sources.id) mà user thấy."""
    with engine.begin() as conn:
        as_user(conn, user_id)
        messages = set(conn.scalars(text("select id from messages")))
        ownership = {
            (r.message_id, r.source_id)
            for r in conn.execute(text("select message_id, source_id from message_ownership"))
        }
        sources = set(conn.scalars(text("select id from mail_sources")))
    return messages, ownership, sources


def test_rls_enabled_flags(owner_engine: Engine, migrated: None) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select relname, relrowsecurity from pg_class "
                "where relkind = 'r' and relname = any(:names)"
            ),
            {"names": list(MAIL_TABLES)},
        )
        flags = {r.relname: r.relrowsecurity for r in rows}
    assert flags == {
        "users": False,
        "mail_sources": True,
        "messages": True,
        "message_ownership": True,
        "threads": True,
        "thread_overrides": True,
        "user_thread_state": True,
        # Không chứa mail; app_api không có quyền gì (chỉ qua hàm SECURITY DEFINER, 0010).
        "user_passwords": False,
        "web_sessions": False,
        # Giai đoạn 5 (0015): vector dẫn xuất từ mail, cài đặt AI và nhật ký gọi Claude.
        "message_chunks": True,
        "user_ai_settings": True,
        # 0016: bí mật (API key mã hóa) của provider AI tùy chỉnh.
        "user_ai_provider": True,
        "ai_calls": True,
        # 0017: dự án và liên kết mail-dự án (spec 25).
        "projects": True,
        "message_projects": True,
        "user_project_state": True,
    }


def test_user_sees_only_own_rows(api_engine: Engine, seeded: SimpleNamespace) -> None:
    s = seeded
    assert visible(api_engine, s.a) == (
        {s.m1, s.m3a, s.m4},
        {(s.m1, s.sA), (s.m3a, s.sA), (s.m4, s.sA), (s.m4, s.sA2)},
        {s.sA, s.sA2},
    )
    assert visible(api_engine, s.b) == (
        {s.m2, s.m3b},
        {(s.m2, s.sB), (s.m3b, s.sB)},
        {s.sB},
    )


def test_message_visible_to_its_user_without_ownership(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    # Mail đã rời mọi folder (không còn ownership) vẫn là của người đó, người khác vẫn không thấy.
    with owner_engine.begin() as conn:
        orphan = add_message(conn, "h-orphan", user_id=seeded.a)
    assert orphan in visible(api_engine, seeded.a)[0]
    assert orphan not in visible(api_engine, seeded.b)[0]


def test_same_message_two_folders_visible_once(api_engine: Engine, seeded: SimpleNamespace) -> None:
    with api_engine.begin() as conn:
        as_user(conn, seeded.a)
        rows = conn.scalars(text("select id from messages where id = :id"), {"id": seeded.m4})
        assert list(rows) == [seeded.m4]


def test_no_user_sees_nothing(api_engine: Engine, seeded: SimpleNamespace) -> None:
    assert visible(api_engine, None) == (set(), set(), set())
    assert visible(api_engine, "") == (set(), set(), set())


def test_api_can_insert_and_delete_only_own_messages(
    api_engine: Engine, seeded: SimpleNamespace
) -> None:
    s = seeded
    with api_engine.begin() as conn:
        as_user(conn, s.a)
        new_id = add_message(conn, "new", user_id=s.a)
        own_deleted = conn.execute(text("delete from messages where id = :id"), {"id": s.m1})
        other_deleted = conn.execute(text("delete from messages where id = :id"), {"id": s.m2})
    assert (own_deleted.rowcount, other_deleted.rowcount) == (1, 0)
    messages = visible(api_engine, s.a)[0]
    assert new_id in messages and s.m1 not in messages
    assert s.m2 in visible(api_engine, s.b)[0]


def test_api_cannot_update_messages(api_engine: Engine, seeded: SimpleNamespace) -> None:
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(text("update messages set subject = 'x' where id = :id"), {"id": seeded.m1})
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)


def test_api_cannot_choose_message_id(api_engine: Engine, seeded: SimpleNamespace) -> None:
    # Tự chọn id (OVERRIDING SYSTEM VALUE) sẽ cho dò/chiếm trước id của người khác.
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(
            text(
                "insert into messages (id, user_id, message_id_hash, parser_version) "
                "overriding system value values (999999, :u, 'x', 't')"
            ),
            {"u": seeded.a},
        )
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)


def insert_message_error(api_engine: Engine, as_id: int, owner_id: int, h: str) -> str:
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, as_id)
        add_message(conn, h, user_id=owner_id)
    orig = ei.value.orig
    assert isinstance(orig, psycopg.errors.InsufficientPrivilege)
    return f"{orig.sqlstate} {orig.diag.message_primary}"


def test_insert_for_other_user_reveals_nothing(api_engine: Engine, seeded: SimpleNamespace) -> None:
    # Cùng một lỗi dù B đã có hash đó ("h2") hay chưa: không dò được mail của B.
    s = seeded
    existing = insert_message_error(api_engine, s.a, s.b, "h2")
    missing = insert_message_error(api_engine, s.a, s.b, "never-seen")
    assert existing == missing


def test_api_cannot_write_for_other_user(api_engine: Engine, seeded: SimpleNamespace) -> None:
    s = seeded
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, s.a)
        add_source(conn, s.b, "Mail/Local Folders/X", kind="local", account="Local Folders")
    assert "row-level security" in str(ei.value.orig)

    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, s.a)
        add_ownership(conn, s.m1, s.b, s.sB)
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)

    with api_engine.begin() as conn:
        as_user(conn, s.a)
        result = conn.execute(
            text("update mail_sources set account = 'x' where id = :id"), {"id": s.sB}
        )
        assert result.rowcount == 0


def test_api_can_manage_own_sources(api_engine: Engine, seeded: SimpleNamespace) -> None:
    s = seeded
    with api_engine.begin() as conn:
        as_user(conn, s.a)
        new_source = add_source(
            conn, s.a, "Mail/Local Folders/X", kind="local", account="Local Folders"
        )
    assert new_source in visible(api_engine, s.a)[2]

    for sql in (
        "insert into mail_sources (id, user_id, kind, account, folder_path) "
        "overriding system value values (999999, :u, 'local', 'L', 'Mail/L/Y')",
        "update mail_sources set user_id = :u where id = :s",
    ):
        with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
            as_user(conn, s.a)
            conn.execute(text(sql), {"u": s.a, "s": s.sA})
        assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege), sql


def test_api_column_privileges(owner_engine: Engine, migrated: None) -> None:
    # Không cột nào cho phép tự chọn id hoặc đổi chủ / đổi liên kết của dòng.
    forbidden = [
        ("messages", "id", "INSERT"),
        ("messages", "user_id", "UPDATE"),
        ("messages", "body_text", "UPDATE"),
        ("mail_sources", "id", "INSERT"),
        ("mail_sources", "id", "UPDATE"),
        ("mail_sources", "user_id", "UPDATE"),
        ("message_ownership", "message_id", "UPDATE"),
        ("message_ownership", "source_id", "UPDATE"),
        ("message_ownership", "user_id", "UPDATE"),
    ]
    with owner_engine.connect() as conn:
        granted = [
            f
            for f in forbidden
            if conn.scalar(
                text("select has_column_privilege('app_api', :t, :c, :p)"),
                {"t": f[0], "c": f[1], "p": f[2]},
            )
        ]
    assert granted == []


def test_api_cannot_claim_other_users_mail(api_engine: Engine, seeded: SimpleNamespace) -> None:
    """app_api không tự gắn mình vào mail của người khác."""
    s = seeded
    # Thêm ownership m2 (của B) vào folder của chính A: FK kép (message_id, user_id) chặn.
    with pytest.raises(IntegrityError) as ei, api_engine.begin() as conn:
        as_user(conn, s.a)
        add_ownership(conn, s.m2, s.a, s.sA2)
    diag = getattr(ei.value.orig, "diag", None)
    assert diag is not None and diag.constraint_name == "fk_message_ownership_message_user"

    # Trỏ dòng ownership của chính A sang m2.
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, s.a)
        conn.execute(
            text(
                "update message_ownership set message_id = :m2 "
                "where message_id = :m1 and source_id = :s"
            ),
            {"m2": s.m2, "m1": s.m1, "s": s.sA},
        )
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)

    assert s.m2 not in visible(api_engine, s.a)[0]


def test_api_can_update_own_flags_only(api_engine: Engine, seeded: SimpleNamespace) -> None:
    s = seeded
    with api_engine.begin() as conn:
        as_user(conn, s.a)
        own = conn.execute(
            text(
                "update message_ownership set is_read = true, is_replied = true, "
                "is_flagged = true where message_id = :m"
            ),
            {"m": s.m3a},
        )
        other = conn.execute(
            text("update message_ownership set is_read = true where source_id = :s"),
            {"s": s.sB},
        )
    assert (own.rowcount, other.rowcount) == (1, 0)


def test_app_user_id_is_parallel_safe(owner_engine: Engine, migrated: None) -> None:
    # Hàm nằm trong mọi policy: không PARALLEL SAFE thì mọi truy vấn trên bảng mail chạy đơn luồng.
    with owner_engine.connect() as conn:
        mode = conn.scalar(text("select proparallel from pg_proc where proname = 'app_user_id'"))
    assert mode == "s"


PGROONGA_ADMIN_FUNCTIONS = (
    "pgroonga_command",
    "pgroonga_set_writable",
    "pgroonga_wal_truncate",
    "pgroonga_wal_apply",
    "pgroonga_flush",
    "pgroonga_vacuum",
)


def test_api_cannot_execute_pgroonga_admin_functions(owner_engine: Engine, migrated: None) -> None:
    # pgroonga_command đọc thẳng dữ liệu index Groonga, không qua RLS.
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select p.oid::regprocedure::text as sig, "
                "has_function_privilege('app_api', p.oid, 'EXECUTE') as allowed "
                "from pg_proc p where p.proname = any(:names)"
            ),
            {"names": list(PGROONGA_ADMIN_FUNCTIONS)},
        ).all()
    assert rows, "không tìm thấy hàm PGroonga nào"
    assert [r.sig for r in rows if r.allowed] == []


def test_worker_sees_everything(worker_engine: Engine, seeded: SimpleNamespace) -> None:
    messages, ownership, sources = visible(worker_engine, None)
    assert len(messages) == 5  # m3 có hai bản (A, B)
    assert len(ownership) == 6
    assert len(sources) == 3


def test_roles_and_table_owner(owner_engine: Engine, migrated: None) -> None:
    with owner_engine.connect() as conn:
        roles = {
            r.rolname: (r.rolsuper, r.rolbypassrls)
            for r in conn.execute(
                text(
                    "select rolname, rolsuper, rolbypassrls from pg_roles "
                    "where rolname in ('app_api', 'app_worker')"
                )
            )
        }
        owners = {
            r.tablename: r.tableowner
            for r in conn.execute(
                text(
                    "select tablename, tableowner from pg_tables "
                    "where schemaname = 'public' and tablename = any(:names)"
                ),
                {"names": list(MAIL_TABLES)},
            )
        }
    assert roles == {"app_api": (False, False), "app_worker": (False, True)}
    assert owners == dict.fromkeys(MAIL_TABLES, "app_owner")
