"""Lỗi DB không làm lộ nội dung dòng vào log; telemetry của FastAPI tắt."""

import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError

from app.db.session import create_api_engine
from app.main import create_app
from tests.dbutil import url_for_test_db


class FakeDiag:
    constraint_name = "ck_example"


class FakePgError(Exception):
    sqlstate = "23514"
    diag = FakeDiag()


async def test_db_error_logged_without_detail(caplog: pytest.LogCaptureFixture) -> None:
    engine = create_api_engine(url_for_test_db("api"))
    # web_dist=None: route thêm sau không bị mount web/dist (bắt mọi đường dẫn) che mất.
    app = create_app(engine, web_dist=None)

    async def boom() -> None:
        raise DBAPIError(
            "insert ...",
            {"subject": "SECRET-SUBJECT"},
            FakePgError("DETAIL: Failing row contains (SECRET-ROW)"),
        )

    app.add_api_route("/boom", boom)
    caplog.set_level(logging.DEBUG)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as c:
        r = await c.get("/boom")
    await engine.dispose()
    assert r.status_code == 500
    assert "SECRET" not in r.text
    assert "SECRET" not in caplog.text
    assert "23514" in caplog.text and "ck_example" in caplog.text


def test_fastapi_telemetry_disabled() -> None:
    app = create_app(create_api_engine(url_for_test_db("api")))
    telemetry = app._telemetry  # noqa: SLF001
    for key in ("tracing", "metrics", "logs", "operation_spans", "auto_configure"):
        assert telemetry.get(key) is False, key
