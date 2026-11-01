"""Bảng glossary, bản dịch, trạng thái tự động (spec 26 mục 3, 8, 9, 10): RLS, GRANT, FK kép."""

from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.dbutil import (
    add_auto_run,
    add_glossary,
    add_project,
    add_translation,
    as_user,
)

TABLES = ("glossary_terms", "message_translations", "message_auto_runs")


def seed_rows(owner_engine: Engine, s: SimpleNamespace) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, s.a, "P")
        add_glossary(conn, s.a, "テスト", "kiểm thử", project_id=pid)
        add_translation(conn, s.a, s.m1)
        add_auto_run(conn, s.a, s.m1)


def test_b_cannot_read_write_a_rows(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    s = seeded
    seed_rows(owner_engine, s)
    for table in TABLES:
        with api_engine.begin() as conn:
            as_user(conn, s.a)
            assert conn.scalar(text(f"select count(*) from {table}")) == 1
        with api_engine.begin() as conn:
            as_user(conn, s.b)
            assert conn.scalar(text(f"select count(*) from {table}")) == 0
    with api_engine.begin() as conn:
        as_user(conn, s.b)
        assert conn.execute(text("update glossary_terms set note = 'x'")).rowcount == 0
        assert conn.execute(text("delete from glossary_terms")).rowcount == 0
        assert conn.execute(text("update message_translations set model = 'x'")).rowcount == 0
        assert conn.execute(text("delete from message_translations")).rowcount == 0
        assert conn.execute(text("update message_auto_runs set attempts = 9")).rowcount == 0
        assert conn.execute(text("delete from message_auto_runs")).rowcount == 0
    # WITH CHECK: B không chèn được dòng mang user_id của A.
    inserts = (
        "insert into glossary_terms (user_id, term, term_norm, translation) "
        "values (:a, 't', 't', 'x')",
        "insert into message_translations (user_id, message_id, translated_subject, "
        "translated_body, source_hash, model, prompt_version) "
        "values (:a, :m, 's', 'b', '\\x00', 'm', 1)",
        "insert into message_auto_runs (user_id, message_id, status, attempts) "
        "values (:a, :m, 'done', 1)",
    )
    for sql in inserts:
        with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
            as_user(conn, s.b)
            conn.execute(text(sql), {"a": s.a, "m": s.m4})
        assert "row-level security" in str(ei.value)


def test_glossary_unique_treats_null_project_as_equal(
    owner_engine: Engine, seeded: SimpleNamespace
) -> None:
    s = seeded
    with owner_engine.begin() as conn:
        p1 = add_project(conn, s.a, "P1")
        p2 = add_project(conn, s.a, "P2")
        add_glossary(conn, s.a, "API", "giao diện")
        add_glossary(conn, s.a, "API", "giao diện", project_id=p1)
        add_glossary(conn, s.a, "api", "giao diện", project_id=p2)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_glossary(conn, s.a, "A P I", "khác")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_glossary(conn, s.a, "API", "khác", project_id=p1)
    with owner_engine.begin() as conn:
        # Người khác cùng term_norm không xung đột.
        add_glossary(conn, s.b, "API", "x")


def test_fk_blocks_other_users_project_and_folder(
    owner_engine: Engine, seeded: SimpleNamespace
) -> None:
    s = seeded
    with owner_engine.begin() as conn:
        pb = add_project(conn, s.b, "PB")
        pa = add_project(conn, s.a, "PA")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_glossary(conn, s.a, "テスト", "x", project_id=pb)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            text("update projects set processed_source_id = :s where id = :p"),
            {"s": s.sB, "p": pa},
        )
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_auto_run(conn, s.a, s.m1, target_source_id=s.sB)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_auto_run(conn, s.a, s.m1, from_source_id=s.sB)
    with owner_engine.begin() as conn:
        add_auto_run(conn, s.a, s.m1, target_source_id=s.sA2)
        conn.execute(
            text("update projects set processed_source_id = :s where id = :p"),
            {"s": s.sA, "p": pa},
        )


def test_auto_run_status_check(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_auto_run(conn, seeded.a, seeded.m1, status="bogus")


def test_delete_message_cascades(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    s = seeded
    seed_rows(owner_engine, s)
    with owner_engine.begin() as conn:
        conn.execute(text("delete from messages where id = :m"), {"m": s.m1})
        assert conn.scalar(text("select count(*) from message_translations")) == 0
        assert conn.scalar(text("select count(*) from message_auto_runs")) == 0


def test_delete_project_cascades_glossary(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    s = seeded
    with owner_engine.begin() as conn:
        pid = add_project(conn, s.a, "P")
        add_glossary(conn, s.a, "テスト", "x", project_id=pid)
        add_glossary(conn, s.a, "共通", "y")
        conn.execute(text("delete from projects where id = :p"), {"p": pid})
        assert conn.scalar(text("select count(*) from glossary_terms")) == 1


def test_delete_source_nulls_targets(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    s = seeded
    with owner_engine.begin() as conn:
        pid = add_project(conn, s.a, "P", processed_source_id=s.sA2)
        add_auto_run(conn, s.a, s.m1, from_source_id=s.sA, target_source_id=s.sA2)
        conn.execute(text("delete from mail_sources where id = :s"), {"s": s.sA2})
        row = conn.execute(
            text("select from_source_id, target_source_id from message_auto_runs")
        ).one()
        assert (row.from_source_id, row.target_source_id) == (s.sA, None)
        got = conn.scalar(
            text("select processed_source_id from projects where id = :p"), {"p": pid}
        )
        assert got is None
        # Xóa from_source_id chỉ đặt NULL cột đó; user_id giữ nguyên.
        conn.execute(text("delete from mail_sources where id = :s"), {"s": s.sA})
        row = conn.execute(text("select user_id, from_source_id from message_auto_runs")).one()
        assert (row.user_id, row.from_source_id) == (s.a, None)


def test_api_grants(
    owner_engine: Engine, api_engine: Engine, worker_engine: Engine, seeded: SimpleNamespace
) -> None:
    s = seeded
    seed_rows(owner_engine, s)
    with api_engine.begin() as conn:
        as_user(conn, s.a)
        conn.execute(text("update glossary_terms set note = 'n'"))
        conn.execute(text("update message_translations set model = 'm2'"))
        conn.execute(text("update message_auto_runs set attempts = 2"))
        conn.execute(text("update projects set customer_domains = '{a.example.jp}'"))
        conn.execute(text("update projects set ai_since = now(), processed_source_id = null"))
    for sql in (
        "update glossary_terms set user_id = user_id",
        "update glossary_terms set id = default",
        "update message_translations set user_id = user_id",
        "update message_translations set message_id = message_id",
        "update message_auto_runs set user_id = user_id",
        "update message_auto_runs set message_id = message_id",
    ):
        with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
            as_user(conn, s.a)
            conn.execute(text(sql))
        assert "permission denied" in str(ei.value)
    updates = {
        "glossary_terms": "update glossary_terms set note = 'x'",
        "message_translations": "update message_translations set model = 'x'",
        "message_auto_runs": "update message_auto_runs set attempts = 3",
    }
    with worker_engine.begin() as conn:  # quét người dùng cần dịch chỉ cần bảng này
        conn.execute(text("select count(*) from message_auto_runs"))
    for table in ("glossary_terms", "message_translations"):
        with pytest.raises(ProgrammingError) as ei, worker_engine.begin() as conn:
            conn.execute(text(f"select count(*) from {table}"))
        assert "permission denied" in str(ei.value)
    for table in TABLES:
        for sql in (f"delete from {table}", updates[table], f"insert into {table} default values"):
            with pytest.raises(ProgrammingError) as ei, worker_engine.begin() as conn:
                conn.execute(text(sql))
            assert "permission denied" in str(ei.value)
