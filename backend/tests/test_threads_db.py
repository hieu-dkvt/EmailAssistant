"""Bảng threads, thread_overrides và các cột làm sạch / gom thread của messages (migration 0006)."""

from types import SimpleNamespace

import psycopg.errors
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.dbutil import add_message, add_thread, as_user


def constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


@pytest.fixture
def threaded(owner_engine: Engine, seeded: SimpleNamespace) -> SimpleNamespace:
    """seeded + thread tA của A (chứa m1, m4), tB của B (chứa m2), override split của A."""
    s = seeded
    with owner_engine.begin() as conn:
        s.tA = add_thread(conn, s.a, 2)
        s.tB = add_thread(conn, s.b, 1)
        conn.execute(
            text("update messages set thread_id = :t where id = any(:ids)"),
            {"t": s.tA, "ids": [s.m1, s.m4]},
        )
        conn.execute(
            text("update messages set thread_id = :t where id = :id"), {"t": s.tB, "id": s.m2}
        )
        s.oA = conn.scalar(
            text(
                "insert into thread_overrides (user_id, action, message_id) "
                "values (:u, 'split', :m) returning id"
            ),
            {"u": s.a, "m": s.m4},
        )
    return s


def visible_threads(engine: Engine, user_id: int | str | None) -> tuple[set[int], set[int]]:
    with engine.begin() as conn:
        as_user(conn, user_id)
        threads = set(conn.scalars(text("select id from threads")))
        overrides = set(conn.scalars(text("select id from thread_overrides")))
    return threads, overrides


def test_user_sees_only_own_threads_and_overrides(
    api_engine: Engine, threaded: SimpleNamespace
) -> None:
    s = threaded
    assert visible_threads(api_engine, s.a) == ({s.tA}, {s.oA})
    assert visible_threads(api_engine, s.b) == ({s.tB}, set())
    assert visible_threads(api_engine, None) == (set(), set())


@pytest.mark.parametrize(
    "sql",
    [
        "insert into threads (user_id, message_count, threader_version) values (:u, 1, 1)",
        "update threads set message_count = 9 where id = :t",
        "delete from threads where id = :t",
        # Từ 0011 app_api được INSERT/DELETE thread_overrides của chính mình (sửa tay từ web,
        # test ở test_web_api.py); vẫn không được UPDATE.
        "update thread_overrides set action = 'split'",
        "insert into thread_overrides (id, user_id, action, message_id) "
        "overriding system value values (1, :u, 'split', :m)",
        "update messages set thread_id = null where id = :m",
        "update messages set body_clean = 'x' where id = :m",
    ],
)
def test_api_cannot_write_threads_or_processing_columns(
    api_engine: Engine, threaded: SimpleNamespace, sql: str
) -> None:
    s = threaded
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, s.a)
        conn.execute(text(sql), {"u": s.a, "t": s.tA, "m": s.m1})
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)


def test_api_cannot_insert_processing_columns(owner_engine: Engine, migrated: None) -> None:
    columns = (
        "body_clean",
        "clean_cuts",
        "cleaner_version",
        "thread_id",
        "thread_parent_id",
        "thread_method",
    )
    with owner_engine.connect() as conn:
        granted = [
            c
            for c in columns
            for p in ("INSERT", "UPDATE")
            if conn.scalar(
                text("select has_column_privilege('app_api', 'messages', :c, :p)"),
                {"c": c, "p": p},
            )
        ]
    assert granted == []


def test_message_thread_must_belong_to_same_user(
    owner_engine: Engine, threaded: SimpleNamespace
) -> None:
    s = threaded
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        conn.execute(
            text("update messages set thread_id = :t where id = :m"), {"t": s.tB, "m": s.m1}
        )
    assert constraint_name(ei.value) == "fk_messages_thread_user"

    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        conn.execute(
            text("update messages set thread_parent_id = :p where id = :m"),
            {"p": s.m2, "m": s.m1},
        )
    assert constraint_name(ei.value) == "fk_messages_thread_parent_user"


def test_override_must_reference_own_messages(
    owner_engine: Engine, threaded: SimpleNamespace
) -> None:
    s = threaded
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        conn.execute(
            text(
                "insert into thread_overrides (user_id, action, message_id, target_message_id) "
                "values (:u, 'merge', :m, :t)"
            ),
            {"u": s.a, "m": s.m1, "t": s.m2},
        )
    assert constraint_name(ei.value) == "fk_thread_overrides_target_user"


@pytest.mark.parametrize(
    ("action", "target", "constraint"),
    [
        ("merge", None, "ck_thread_overrides_target"),
        ("split", "m3a", "ck_thread_overrides_target"),
        ("merge", "m1", "ck_thread_overrides_not_self"),
        ("join", None, "ck_thread_overrides_action"),
    ],
)
def test_override_checks(
    owner_engine: Engine,
    threaded: SimpleNamespace,
    action: str,
    target: str | None,
    constraint: str,
) -> None:
    s = threaded
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        conn.execute(
            text(
                "insert into thread_overrides (user_id, action, message_id, target_message_id) "
                "values (:u, :a, :m, :t)"
            ),
            {"u": s.a, "a": action, "m": s.m1, "t": getattr(s, target) if target else None},
        )
    assert constraint_name(ei.value) == constraint


def test_thread_method_check(owner_engine: Engine, threaded: SimpleNamespace) -> None:
    with pytest.raises(IntegrityError) as ei, owner_engine.begin() as conn:
        conn.execute(
            text("update messages set thread_method = 'guess' where id = :m"),
            {"m": threaded.m1},
        )
    assert constraint_name(ei.value) == "ck_messages_thread_method"


def test_delete_thread_keeps_messages(owner_engine: Engine, threaded: SimpleNamespace) -> None:
    s = threaded
    with owner_engine.begin() as conn:
        conn.execute(text("delete from threads where id = :t"), {"t": s.tA})
        rows = conn.execute(
            text("select user_id, thread_id from messages where id = any(:ids) order by id"),
            {"ids": [s.m1, s.m4]},
        ).all()
    assert [tuple(r) for r in rows] == [(s.a, None), (s.a, None)]


def test_delete_message_clears_children_and_overrides(
    owner_engine: Engine, api_engine: Engine, threaded: SimpleNamespace
) -> None:
    s = threaded
    with owner_engine.begin() as conn:
        child = add_message(conn, "h-child", user_id=s.a, thread_parent_id=s.m4)
    # Xóa bằng app_api (như chức năng xóa mail của folder): FK SET NULL / CASCADE vẫn chạy.
    with api_engine.begin() as conn:
        as_user(conn, s.a)
        conn.execute(text("delete from messages where id = :m"), {"m": s.m4})
    with owner_engine.connect() as conn:
        parent = conn.scalar(
            text("select thread_parent_id from messages where id = :m"), {"m": child}
        )
        overrides = conn.scalar(text("select count(*) from thread_overrides"))
    assert (parent, overrides) == (None, 0)


def test_worker_allocates_thread_ids(worker_engine: Engine, threaded: SimpleNamespace) -> None:
    with worker_engine.begin() as conn:
        ids = list(
            conn.scalars(
                text(
                    "select nextval(pg_get_serial_sequence('threads', 'id')) "
                    "from generate_series(1, 3)"
                )
            )
        )
        conn.execute(
            text(
                "insert into threads (id, user_id, message_count, threader_version) "
                "overriding system value select x, :u, 1, 1 from unnest(cast(:ids as bigint[])) x"
            ),
            {"u": threaded.a, "ids": ids},
        )
        count = conn.scalar(text("select count(*) from threads where id = any(:ids)"), {"ids": ids})
    assert count == 3
