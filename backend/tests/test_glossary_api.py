"""API glossary (spec 26 mục 9). Dữ liệu giả."""

import csv
import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import create_api_engine
from tests.dbutil import add_project, url_for_test_db
from tests.webutil import CSRF, add_web_user, login, web_client

URL = "/api/v1/glossary"
SEED_DIR = Path(__file__).parents[1] / "seed"
SEEDS = ("glossary-it-ja-vi.csv", "glossary-programming-ja-vi.csv")


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


@pytest.fixture
def project_a(owner_engine: Engine, users: tuple[int, int]) -> int:
    with owner_engine.begin() as conn:
        return add_project(conn, users[0], "P")


def count(owner_engine: Engine) -> int:
    with owner_engine.connect() as conn:
        return conn.scalar(text("select count(*) from glossary_terms")) or 0


async def add(c: AsyncClient, term: str, translation: str, **extra: Any) -> Any:
    body = {"term": term, "translation": translation, **extra}
    return await c.post(URL, json=body, headers=CSRF)


async def imp(c: AsyncClient, body: str, **extra: Any) -> Any:
    payload = {
        "project_id": None,
        "text": body,
        "format": "csv",
        "on_conflict": "skip",
        "apply": True,
        **extra,
    }
    return await c.post(f"{URL}/import", json=payload, headers=CSRF)


async def test_crud(ca: AsyncClient, project_a: int) -> None:
    r = await add(ca, "検収", "nghiệm thu", note="giai đoạn cuối")
    assert r.status_code == 201
    item = r.json()
    assert item["project_id"] is None and item["note"] == "giai đoạn cuối"
    r = await add(ca, "検収", "chấp nhận", project_id=project_a)
    assert r.status_code == 201
    common = (await ca.get(URL)).json()
    assert common["total"] == 1 and common["items"][0]["id"] == item["id"]
    scoped = (await ca.get(URL, params={"project_id": project_a})).json()
    assert scoped["total"] == 1 and scoped["items"][0]["translation"] == "chấp nhận"
    r = await ca.patch(
        f"{URL}/{item['id']}", json={"term": "検収", "translation": "nghiệm thu mới"}, headers=CSRF
    )
    assert r.status_code == 200 and r.json()["translation"] == "nghiệm thu mới"
    assert r.json()["note"] is None
    assert (await ca.delete(f"{URL}/{item['id']}", headers=CSRF)).status_code == 204
    assert (await ca.get(URL)).json()["total"] == 0
    assert (await ca.delete(f"{URL}/{item['id']}", headers=CSRF)).status_code == 404


async def test_search_and_paging(ca: AsyncClient) -> None:
    for t, tr in [("API", "API"), ("テスト", "kiểm thử"), ("仕様書", "tài liệu đặc tả")]:
        await add(ca, t, tr)
    got = (await ca.get(URL, params={"q": "ＡＰＩ"})).json()
    assert got["total"] == 1 and got["items"][0]["term"] == "API"
    got = (await ca.get(URL, params={"q": "đặc tả"})).json()
    assert [i["term"] for i in got["items"]] == ["仕様書"]
    got = (await ca.get(URL, params={"limit": 2, "offset": 2})).json()
    assert got["total"] == 3 and len(got["items"]) == 1
    assert (await ca.get(URL, params={"limit": 0})).status_code == 422


async def test_validation(ca: AsyncClient) -> None:
    assert (await add(ca, "a", "x")).status_code == 422
    assert (await add(ca, "ab", "")).status_code == 422
    assert (await add(ca, "ab" * 101, "x")).status_code == 422
    assert (await add(ca, "ab", "x" * 201)).status_code == 422
    assert (await add(ca, "ab", "x", note="n" * 501)).status_code == 422
    assert (await add(ca, "a\x01b", "x")).status_code == 422


async def test_duplicate_409(ca: AsyncClient, project_a: int) -> None:
    assert (await add(ca, "API", "a")).status_code == 201
    r = await add(ca, "ＡＰＩ", "b")
    assert r.status_code == 409 and r.json()["code"] == "glossary_duplicate"
    assert (await add(ca, "API", "b", project_id=project_a)).status_code == 201
    other = (await add(ca, "SQL", "s")).json()["id"]
    r = await ca.patch(f"{URL}/{other}", json={"term": "api", "translation": "s"}, headers=CSRF)
    assert r.status_code == 409 and r.json()["code"] == "glossary_duplicate"


async def test_import_preview_writes_nothing(ca: AsyncClient, owner_engine: Engine) -> None:
    await add(ca, "API", "a")
    r = await imp(ca, "term,translation,note\nAPI,x,\nSQL,y,\nbad\n", apply=False)
    assert r.status_code == 200
    got = r.json()
    assert (got["added"], got["duplicate"], got["invalid"]) == (1, 1, 1)
    assert got["first_error"].startswith("Dòng 4")
    assert count(owner_engine) == 1


async def test_import_skip_and_overwrite(ca: AsyncClient) -> None:
    await add(ca, "API", "cũ", note="n")
    body = "term,translation,note\nAPI,mới,\nSQL,csdl,\nsql,dup,\n"
    r = await imp(ca, body, on_conflict="skip")
    assert r.json()["added"] == 1 and r.json()["duplicate"] == 2
    items = {i["term"]: i for i in (await ca.get(URL)).json()["items"]}
    assert items["API"]["translation"] == "cũ" and items["SQL"]["translation"] == "csdl"
    r = await imp(ca, body, on_conflict="overwrite")
    assert r.json()["added"] == 0 and r.json()["duplicate"] == 3
    items = {i["term"]: i for i in (await ca.get(URL)).json()["items"]}
    assert items["API"]["translation"] == "mới" and items["API"]["note"] is None
    assert items["sql"]["translation"] == "dup"


async def test_import_into_project(ca: AsyncClient, project_a: int) -> None:
    r = await imp(ca, "API,a\n", project_id=project_a)
    assert r.json()["added"] == 1
    assert (await ca.get(URL, params={"project_id": project_a})).json()["total"] == 1
    assert (await ca.get(URL)).json()["total"] == 0


async def test_import_lines_format(ca: AsyncClient) -> None:
    body = "検収 = nghiệm thu\n\nAPI=giao diện\nkhông có dấu bằng\n x = \n"
    r = await imp(ca, body, format="lines", apply=False)
    assert r.json()["added"] == 2 and r.json()["invalid"] == 2
    r = await imp(ca, body, format="lines")
    assert r.json()["added"] == 2
    got = {i["term"]: i["translation"] for i in (await ca.get(URL)).json()["items"]}
    assert got == {"検収": "nghiệm thu", "API": "giao diện"}


async def test_import_bad_row_reported(ca: AsyncClient) -> None:
    secret = "BI-MAT-KHACH-HANG"
    r = await imp(ca, f"ok,fine\n{secret}\n")
    body = r.json()
    assert body["added"] == 1 and body["invalid"] == 1
    assert body["first_error"].startswith("Dòng 2")
    assert secret not in r.text
    r = await imp(ca, f"ok2,fine\nxx,{'y' * 300}\n")
    assert r.json()["invalid"] == 1 and "yyyy" not in r.json()["first_error"]


async def test_import_limits(ca: AsyncClient, owner_engine: Engine) -> None:
    r = await imp(ca, "\n".join(f"t{i:05d},x" for i in range(5001)))
    assert r.status_code == 422 and r.json()["code"] == "glossary_import_too_many_lines"
    r = await imp(ca, "a" * (1024 * 1024 + 1))
    assert r.status_code == 413 and r.json()["code"] == "glossary_import_too_large"
    assert count(owner_engine) == 0
    r = await imp(ca, "\n".join(f"t{i:05d},x" for i in range(5000)))
    assert r.json()["added"] == 5000
    r = await add(ca, "extra", "x")
    assert r.status_code == 409 and r.json()["code"] == "glossary_limit"
    r = await imp(ca, "extra,x")
    assert r.status_code == 409 and r.json()["code"] == "glossary_limit"
    assert count(owner_engine) == 5000


async def test_export_escapes_formula_roundtrip(ca: AsyncClient) -> None:
    await add(ca, "=SUM(A1)", "+1", note="@x")
    await add(ca, "'=ab", "-tr")
    r = await ca.get(f"{URL}/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    exported = r.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(exported)))
    assert rows[0] == ["term", "translation", "note"]
    assert ["'=SUM(A1)", "'+1", "'@x"] in rows
    for i in (await ca.get(URL)).json()["items"]:
        await ca.delete(f"{URL}/{i['id']}", headers=CSRF)
    assert (await imp(ca, exported)).json()["added"] == 2
    got = {i["term"]: (i["translation"], i["note"]) for i in (await ca.get(URL)).json()["items"]}
    assert got == {"=SUM(A1)": ("+1", "@x"), "'=ab": ("-tr", None)}


@pytest.mark.parametrize("value", ["＝SUM(A1)", "＋1", "－1", "＠x"])
async def test_export_escapes_full_width_formula_chars(ca: AsyncClient, value: str) -> None:
    await add(ca, value, "x")
    r = await ca.get(f"{URL}/export")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[1][0] == "'" + value
    await ca.delete(f"{URL}/{(await ca.get(URL)).json()['items'][0]['id']}", headers=CSRF)
    assert (await imp(ca, r.content.decode("utf-8-sig"))).json()["added"] == 1
    assert (await ca.get(URL)).json()["items"][0]["term"] == value


async def test_import_whole_export_file_with_bom(ca: AsyncClient) -> None:
    """Nhập nguyên file vừa xuất, giữ BOM đầu file: dòng tiêu đề không thành dữ liệu."""
    await add(ca, "検収", "nghiệm thu")
    exported = (await ca.get(f"{URL}/export")).content.decode("utf-8")
    assert exported.startswith("﻿")
    item = (await ca.get(URL)).json()["items"][0]
    await ca.delete(f"{URL}/{item['id']}", headers=CSRF)
    r = await imp(ca, exported)
    assert r.json()["added"] == 1 and r.json()["invalid"] == 0
    assert [i["term"] for i in (await ca.get(URL)).json()["items"]] == ["検収"]


async def test_import_lines_with_bom(ca: AsyncClient) -> None:
    r = await imp(ca, "﻿検収 = nghiệm thu\n", format="lines")
    assert r.json()["added"] == 1
    assert [i["term"] for i in (await ca.get(URL)).json()["items"]] == ["検収"]


async def test_export_has_bom(ca: AsyncClient) -> None:
    await add(ca, "検収", "nghiệm thu")
    r = await ca.get(f"{URL}/export")
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "検収" in r.content.decode("utf-8-sig")


async def test_project_of_other_user_404(ca: AsyncClient, cb: AsyncClient, project_a: int) -> None:
    for r in [
        await add(cb, "API", "x", project_id=project_a),
        await cb.get(URL, params={"project_id": project_a}),
        await cb.get(f"{URL}/export", params={"project_id": project_a}),
        await imp(cb, "API,x", project_id=project_a),
    ]:
        assert r.status_code == 404 and r.json()["code"] == "project_not_found"


async def test_other_user_item_hidden(ca: AsyncClient, cb: AsyncClient) -> None:
    gid = (await add(ca, "API", "x")).json()["id"]
    assert (await cb.get(URL)).json()["total"] == 0
    r = await cb.patch(f"{URL}/{gid}", json={"term": "API", "translation": "y"}, headers=CSRF)
    assert r.status_code == 404
    assert (await cb.delete(f"{URL}/{gid}", headers=CSRF)).status_code == 404
    assert (await ca.get(URL)).json()["items"][0]["translation"] == "x"


async def test_requires_session_and_csrf(api: AsyncEngine, ca: AsyncClient) -> None:
    assert (await ca.post(URL, json={"term": "ab", "translation": "x"})).status_code == 403
    assert (await ca.patch(f"{URL}/1", json={"term": "ab", "translation": "x"})).status_code == 403
    assert (await ca.delete(f"{URL}/1")).status_code == 403
    body = {"text": "a,b", "format": "csv"}
    assert (await ca.post(f"{URL}/import", json=body)).status_code == 403
    async with web_client(api) as c:
        assert (await c.get(URL)).status_code == 401
        assert (await c.get(f"{URL}/export")).status_code == 401
        assert (await add(c, "ab", "x")).status_code == 401
        assert (await imp(c, "ab,x")).status_code == 401
        assert (await c.delete(f"{URL}/1", headers=CSRF)).status_code == 401


@pytest.mark.parametrize("name", SEEDS)
async def test_seed_csv_imports_cleanly(ca: AsyncClient, name: str) -> None:
    r = await imp(ca, (SEED_DIR / name).read_text(encoding="utf-8"), apply=False)
    assert r.status_code == 200
    result = r.json()
    assert result["invalid"] == 0 and result["duplicate"] == 0
    assert 100 <= result["added"] <= 200


async def test_seed_files_do_not_overlap(ca: AsyncClient) -> None:
    """Nạp lần lượt các file hạt giống vào glossary chung: không thuật ngữ nào trùng nhau."""
    for name in SEEDS:
        r = await imp(ca, (SEED_DIR / name).read_text(encoding="utf-8"), apply=True)
        assert r.status_code == 200
        assert r.json()["duplicate"] == 0, name
