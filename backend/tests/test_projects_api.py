"""API cấu hình dự án (spec 2026-10-31-project-settings-design mục 6). Dữ liệu giả *.example.*."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import create_api_engine, user_session
from app.projects import create_project
from app.web_schemas import ProjectInput
from tests.dbutil import add_project, url_for_test_db
from tests.webutil import CSRF, add_web_user, login, web_client

FILTERS = {"filter_from": ["@sample-a.example.jp"], "filter_subject": ["【サンプルA】"]}
URL = "/api/v1/projects"


@pytest.fixture
async def api() -> AsyncIterator[AsyncEngine]:
    engine = create_api_engine(url_for_test_db("api"))
    yield engine
    await engine.dispose()


@pytest.fixture
def users(owner_engine: Engine, clean: None) -> tuple[int, int]:
    return add_web_user(owner_engine, "a@example.com"), add_web_user(owner_engine, "b@example.com")


@pytest.fixture
async def ca(api: AsyncEngine, users: tuple[int, int]) -> AsyncIterator[AsyncClient]:
    async with web_client(api) as c:
        await login(c, "a@example.com")
        yield c


@pytest.fixture
async def cb(api: AsyncEngine, users: tuple[int, int]) -> AsyncIterator[AsyncClient]:
    async with web_client(api) as c:
        await login(c, "b@example.com")
        yield c


def version(owner_engine: Engine, user_id: int) -> int:
    with owner_engine.connect() as conn:
        return (
            conn.scalar(
                text("select rules_version from user_project_state where user_id = :u"),
                {"u": user_id},
            )
            or 0
        )


async def post(c: AsyncClient, body: dict[str, Any]) -> Any:
    return await c.post(URL, json=body, headers=CSRF)


async def test_create_list_update_delete(ca: AsyncClient) -> None:
    r = await post(ca, {"name": "Dự án A", **FILTERS})
    assert r.status_code == 201
    created = r.json()
    assert created["filter_from"] == ["@sample-a.example.jp"] and created["redmine_url"] is None
    listed = (await ca.get(URL)).json()
    assert [p["id"] for p in listed["items"]] == [created["id"]]
    r = await ca.put(
        f"{URL}/{created['id']}",
        json={"name": "Dự án A2", "redmine_url": "https://redmine.example.jp/"},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Dự án A2" and r.json()["filter_from"] == []
    r = await ca.delete(f"{URL}/{created['id']}", headers=CSRF)
    assert r.status_code == 204
    assert (await ca.get(URL)).json()["items"] == []


async def test_create_without_filter_keeps_version(
    ca: AsyncClient, owner_engine: Engine, users: tuple[int, int]
) -> None:
    assert (await post(ca, {"name": "P"})).status_code == 201
    assert version(owner_engine, users[0]) == 0
    assert (await ca.get(URL)).json()["assigning"] is False


async def test_create_with_filter_bumps_version(
    ca: AsyncClient, owner_engine: Engine, users: tuple[int, int]
) -> None:
    assert (await post(ca, {"name": "P", **FILTERS})).status_code == 201
    assert version(owner_engine, users[0]) == 1
    assert (await ca.get(URL)).json()["assigning"] is True


async def test_update_filter_bumps_version(
    ca: AsyncClient, owner_engine: Engine, users: tuple[int, int]
) -> None:
    pid = (await post(ca, {"name": "P", **FILTERS})).json()["id"]
    r = await ca.put(
        f"{URL}/{pid}", json={"name": "P", "filter_to": ["x@example.jp"]}, headers=CSRF
    )
    assert r.status_code == 200
    assert version(owner_engine, users[0]) == 2


async def test_update_other_field_keeps_version(
    ca: AsyncClient, owner_engine: Engine, users: tuple[int, int]
) -> None:
    pid = (await post(ca, {"name": "P", **FILTERS})).json()["id"]
    r = await ca.put(
        f"{URL}/{pid}",
        json={"name": "P2", **FILTERS, "redmine_url": "https://redmine.example.jp"},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert version(owner_engine, users[0]) == 1


async def test_delete_keeps_version(
    ca: AsyncClient, owner_engine: Engine, users: tuple[int, int]
) -> None:
    pid = (await post(ca, {"name": "P", **FILTERS})).json()["id"]
    assert (await ca.delete(f"{URL}/{pid}", headers=CSRF)).status_code == 204
    assert version(owner_engine, users[0]) == 1


async def test_name_taken_409(ca: AsyncClient, cb: AsyncClient) -> None:
    assert (await post(ca, {"name": "P"})).status_code == 201
    r = await post(ca, {"name": "P"})
    assert r.status_code == 409 and r.json()["code"] == "project_name_taken"
    other = (await post(ca, {"name": "Q"})).json()["id"]
    r = await ca.put(f"{URL}/{other}", json={"name": "P"}, headers=CSRF)
    assert r.status_code == 409 and r.json()["code"] == "project_name_taken"
    # Người khác đặt cùng tên được (unique theo user_id).
    assert (await post(cb, {"name": "P"})).status_code == 201


async def test_name_taken_on_concurrent_save(
    ca: AsyncClient, api: AsyncEngine, users: tuple[int, int]
) -> None:
    """Hai request lưu cùng tên cùng lúc: bên thua nhận 409, không phải 500."""
    async with user_session(api, users[0]) as s:
        await create_project(s, users[0], ProjectInput(name="Race"))  # chưa commit
        task = asyncio.create_task(post(ca, {"name": "Race"}))
        await asyncio.sleep(0.5)  # request kia đang chờ khóa unique của dòng chưa commit
        assert not task.done()
    r = await task
    assert r.status_code == 409 and r.json()["code"] == "project_name_taken"


async def test_project_limit_409(
    ca: AsyncClient, owner_engine: Engine, users: tuple[int, int]
) -> None:
    with owner_engine.begin() as conn:
        for i in range(100):
            add_project(conn, users[0], f"P{i}")
    r = await post(ca, {"name": "extra"})
    assert r.status_code == 409 and r.json()["code"] == "project_limit"


async def test_other_user_gets_404(
    ca: AsyncClient, cb: AsyncClient, owner_engine: Engine, users: tuple[int, int]
) -> None:
    pid = (await post(ca, {"name": "P", **FILTERS})).json()["id"]
    r = await cb.put(f"{URL}/{pid}", json={"name": "X", "filter_to": ["z"]}, headers=CSRF)
    assert r.status_code == 404 and r.json()["code"] == "project_not_found"
    r = await cb.delete(f"{URL}/{pid}", headers=CSRF)
    assert r.status_code == 404 and r.json()["code"] == "project_not_found"
    assert (await cb.get(URL)).json()["items"] == []
    assert version(owner_engine, users[1]) == 0
    assert len((await ca.get(URL)).json()["items"]) == 1


async def test_write_requires_csrf_header(ca: AsyncClient) -> None:
    assert (await ca.post(URL, json={"name": "P"})).status_code == 403
    pid = (await post(ca, {"name": "P"})).json()["id"]
    assert (await ca.put(f"{URL}/{pid}", json={"name": "P"})).status_code == 403
    assert (await ca.delete(f"{URL}/{pid}")).status_code == 403


async def test_requires_session(api: AsyncEngine, users: tuple[int, int]) -> None:
    async with web_client(api) as c:
        assert (await c.get(URL)).status_code == 401
        assert (await post(c, {"name": "P"})).status_code == 401
        assert (await c.put(f"{URL}/1", json={"name": "P"}, headers=CSRF)).status_code == 401
        assert (await c.delete(f"{URL}/1", headers=CSRF)).status_code == 401


async def test_collector_token_rejected(
    api: AsyncEngine, owner_engine: Engine, users: tuple[int, int]
) -> None:
    from app.cli import create_user

    with owner_engine.begin() as conn:
        _, token = create_user(conn, "tok@example.com", "tok")
    async with web_client(api) as c:
        r = await c.get(URL, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


async def test_id_out_of_range_422(ca: AsyncClient) -> None:
    big = 9999999999999999999
    assert (await ca.put(f"{URL}/{big}", json={"name": "P"}, headers=CSRF)).status_code == 422
    assert (await ca.delete(f"{URL}/{big}", headers=CSRF)).status_code == 422


SECRET = "sekret-value-77"


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "  "},
        {"name": "a\x01"},
        {"name": "n" * 201},
        {"filter_from": [SECRET] * 21},
        {"filter_from": [SECRET * 30]},
        {"filter_to": [SECRET, "  "]},
        {"filter_cc": [""]},
        {"filter_subject": [chr(0x3000)]},  # khoảng trắng toàn chiều rộng
        {"filter_subject": [f"{SECRET}\x07"]},
        {"redmine_url": f"https://user:{SECRET}@redmine.example"},
        {"redmine_url": f"https://{SECRET}@redmine.example"},
        {"docs_repo_url": f"ftp://{SECRET}.example"},
        {"docs_repo_url": f"ssh://git:{SECRET}@git.example/a.git"},
        {"docs_repo_url": f"git@:{SECRET}"},
        {"docs_repo_url": f"https://{SECRET}.example/ a"},
        {"docs_local_path": "p" * 1001},
        {"backlog_project_key": "k" * 201},
        {"unknown": SECRET},
    ],
)
async def test_invalid_422_without_echo(ca: AsyncClient, bad: dict[str, Any]) -> None:
    r = await post(ca, {"name": "P", **bad})
    assert r.status_code == 422
    assert SECRET not in r.text
    if "unknown" not in bad:
        field = next(iter(bad))
        assert all(e["loc"][0] == field for e in r.json()["detail"]), r.text
    assert (await ca.get(URL)).json()["items"] == []


async def test_values_trimmed_and_deduplicated(ca: AsyncClient) -> None:
    r = await post(
        ca,
        {
            "name": "  P  ",
            "filter_from": [" a@x.example ", "A@X.EXAMPLE", "ａ＠x.example", "b@x.example"],
            "docs_local_path": "  ",
            "redmine_project": " rm ",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "P"
    assert body["filter_from"] == ["a@x.example", "b@x.example"]
    assert body["docs_local_path"] is None and body["redmine_project"] == "rm"


async def test_git_ssh_url_accepted(ca: AsyncClient) -> None:
    r = await post(
        ca,
        {
            "name": "P",
            "docs_repo_url": "git@git.example:team/spec.git",
            "review_repo_url": "ssh://git@git.example/team/review.git",
            "backlog_space_url": "http://backlog.example.jp",
        },
    )
    assert r.status_code == 201
    assert r.json()["docs_repo_url"] == "git@git.example:team/spec.git"


async def test_filter_values_not_logged(ca: AsyncClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    r = await post(ca, {"name": "Tên-bí-mật-khách", "filter_from": ["bimat@client.example"]})
    pid = r.json()["id"]
    await ca.put(
        f"{URL}/{pid}", json={"name": "Tên-bí-mật-khách", "filter_to": ["bimat2"]}, headers=CSRF
    )
    await post(ca, {"name": "Tên-bí-mật-khách"})  # 409
    await ca.delete(f"{URL}/{pid}", headers=CSRF)
    assert "bimat" not in caplog.text and "Tên-bí-mật-khách" not in caplog.text
