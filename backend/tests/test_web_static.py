"""Backend phục vụ bản build của web, header bảo mật, xuất OpenAPI (spec 16 mục 7)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import openapi_dump
from app.db.session import create_api_engine
from app.main import create_app
from tests.dbutil import url_for_test_db

INDEX = '<!doctype html><html lang="vi" translate="no"><div id="root"></div></html>'


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


async def make_client(web_dist: Path | None) -> AsyncIterator[AsyncClient]:
    engine = create_api_engine(url_for_test_db("api"))
    app = create_app(engine, web_dist=web_dist)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest.fixture
async def client(dist: Path) -> AsyncIterator[AsyncClient]:
    async for c in make_client(dist):
        yield c


async def test_spa_routes_serve_index(client: AsyncClient) -> None:
    for path in ("/", "/threads", "/threads/12", "/search?q=x", "/docs"):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert r.text == INDEX
        assert r.headers["cache-control"] == "no-cache"


async def test_assets(client: AsyncClient) -> None:
    r = await client.get("/assets/app-abc123.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert "immutable" in r.headers["cache-control"]
    r = await client.get("/assets/missing.js")
    assert r.status_code == 404
    assert "immutable" not in r.headers["cache-control"]


async def test_api_paths_never_serve_index(client: AsyncClient) -> None:
    for path in ("/api", "/api/", "/api/v1/khong-co", "/api/v1/threads/abc"):
        r = await client.get(path)
        assert r.status_code in (404, 401, 422), path
        assert INDEX not in r.text
        assert r.headers["cache-control"] == "no-store"
    r = await client.get("/api/v1/khong-co")
    assert r.json() == {"detail": "Not Found"}


async def test_security_headers(client: AsyncClient) -> None:
    for path in ("/", "/api/v1/session"):
        r = await client.get(path)
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "http" not in csp  # không cho tải gì từ bên ngoài
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"


async def test_foreign_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # DNS rebinding: tên miền lạ trỏ về 127.0.0.1 → 400, không tới API.
    monkeypatch.delenv("PMM_ALLOWED_HOSTS")
    engine = create_api_engine(url_for_test_db("api"))
    app = create_app(engine, web_dist=None)
    try:
        for base, code in (
            ("http://evil.example.com", 400),
            ("http://127.0.0.1:8000", 401),
            ("http://localhost:8000", 401),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url=base) as c:
                r = await c.get("/api/v1/session")
                assert r.status_code == code, base
    finally:
        await engine.dispose()


async def test_without_web_build() -> None:
    async for c in make_client(None):
        assert (await c.get("/")).status_code == 404
        assert (await c.get("/docs")).status_code == 404  # không có Swagger UI (script từ CDN)
        assert (await c.get("/api/v1/session")).status_code == 401


def test_openapi_dump(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert openapi_dump.main([str(out)]) == 0
    spec = json.loads(out.read_text(encoding="utf-8"))
    for path in ("/api/v1/search", "/api/v1/threads/{thread_id}", "/api/v1/session"):
        assert path in spec["paths"]
    assert openapi_dump.main([]) == 2
