from sqlalchemy import Engine, text

from app.config import database_url


def test_test_db_has_extensions(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        names = set(conn.scalars(text("select extname from pg_extension")))
    assert {"pgroonga", "vector"} <= names


def test_api_role_connects_without_bypassrls(api_engine: Engine) -> None:
    with api_engine.connect() as conn:
        row = conn.execute(
            text(
                "select current_user, rolbypassrls, rolsuper "
                "from pg_roles where rolname = current_user"
            )
        ).one()
    assert tuple(row) == ("app_api", False, False)


def test_server_log_is_terse(owner_engine: Engine) -> None:
    # terse: log lỗi của server không kèm DETAIL "Failing row contains (...)" (nội dung dòng/mail).
    with owner_engine.connect() as conn:
        assert conn.scalar(text("show log_error_verbosity")) == "terse"


def test_database_url_env_override(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL_API", "postgresql+psycopg://x:y@h:1/d")
    assert database_url("api") == "postgresql+psycopg://x:y@h:1/d"

    monkeypatch.delenv("DATABASE_URL_API")
    assert database_url("api") == "postgresql+psycopg://app_api:dev-api@127.0.0.1:15432/mailassist"
