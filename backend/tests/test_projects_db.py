"""Bảng dự án và liên kết mail (spec 25 mục 4): RLS, GRANT, FK kép, xóa lan truyền."""

from types import SimpleNamespace

import psycopg.errors
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.dbutil import add_project, as_user


def link(conn, user_id: int, message_id: int, project_id: int) -> None:  # noqa: ANN001
    conn.execute(
        text("insert into message_projects (user_id, message_id, project_id) values (:u, :m, :p)"),
        {"u": user_id, "m": message_id, "p": project_id},
    )


def count(conn, table: str) -> int:  # noqa: ANN001
    return conn.scalar(text(f"select count(*) from {table}"))


def test_project_is_private(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "Dự án A")
    with api_engine.begin() as conn:
        as_user(conn, seeded.a)
        assert list(conn.scalars(text("select id from projects"))) == [pid]
    with api_engine.begin() as conn:
        as_user(conn, seeded.b)
        assert list(conn.scalars(text("select id from projects"))) == []
    with api_engine.begin() as conn:
        assert list(conn.scalars(text("select id from projects"))) == []


def test_api_role_cannot_write_message_projects(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        link(conn, seeded.a, seeded.m1, pid)
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)


def test_api_role_cannot_set_project_id_or_change_owner(
    owner_engine: Engine, api_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(
            text(
                "insert into projects (id, user_id, name) "
                "overriding system value values (999, :u, 'x')"
            ),
            {"u": seeded.a},
        )
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(
            text("update projects set user_id = :b where id = :id"), {"b": seeded.b, "id": pid}
        )
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    with pytest.raises(ProgrammingError) as ei, api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(text("update projects set id = default where id = :id"), {"id": pid})
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege)
    # Sửa tên (cột được phép) thì chạy.
    with api_engine.begin() as conn:
        as_user(conn, seeded.a)
        conn.execute(text("update projects set name = 'Q' where id = :id"), {"id": pid})


def test_message_projects_rejects_other_users_project(
    owner_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid_b = add_project(conn, seeded.b, "PB")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        link(conn, seeded.a, seeded.m1, pid_b)
    # Mail của người khác cũng bị chặn.
    with owner_engine.begin() as conn:
        pid_a = add_project(conn, seeded.a, "PA")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        link(conn, seeded.a, seeded.m2, pid_a)


def test_deleting_message_removes_link(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
        link(conn, seeded.a, seeded.m1, pid)
        conn.execute(text("delete from messages where id = :id"), {"id": seeded.m1})
        assert count(conn, "message_projects") == 0
        assert count(conn, "projects") == 1


def test_deleting_project_removes_link_keeps_message(
    owner_engine: Engine, seeded: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        pid = add_project(conn, seeded.a, "P")
        link(conn, seeded.a, seeded.m1, pid)
        conn.execute(text("delete from projects where id = :id"), {"id": pid})
        assert count(conn, "message_projects") == 0
        assert (
            conn.scalar(text("select count(*) from messages where id = :id"), {"id": seeded.m1})
            == 1
        )


def test_project_name_unique_per_user(owner_engine: Engine, seeded: SimpleNamespace) -> None:
    with owner_engine.begin() as conn:
        add_project(conn, seeded.a, "Chung")
        add_project(conn, seeded.b, "Chung")
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        add_project(conn, seeded.a, "Chung")
