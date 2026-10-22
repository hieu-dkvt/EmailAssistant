"""Helper user_session: mọi truy vấn phục vụ người dùng chạy trong transaction có app.user_id."""

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import create_api_engine, user_session
from tests.dbutil import url_for_test_db

TMP_FOLDER = "Mail/Local Folders/Tmp"
INSERT_TMP_SOURCE = text(
    "insert into mail_sources (user_id, kind, account, folder_path) "
    "values (:u, 'local', 'Local Folders', :p)"
)


async def message_ids(session: AsyncSession) -> set[int]:
    return set((await session.scalars(text("select id from messages"))).all())


async def count_tmp_sources(session: AsyncSession) -> int:
    return await session.scalar(
        text("select count(*) from mail_sources where folder_path = :p"), {"p": TMP_FOLDER}
    )


async def test_user_session_scopes_rows(seeded: SimpleNamespace) -> None:
    engine = create_api_engine(url_for_test_db("api"))
    try:
        async with user_session(engine, seeded.a) as s:
            assert await message_ids(s) == {seeded.m1, seeded.m3a, seeded.m4}
        async with user_session(engine, seeded.b) as s:
            assert await message_ids(s) == {seeded.m2, seeded.m3b}
    finally:
        await engine.dispose()


async def test_setting_does_not_leak_between_sessions(seeded: SimpleNamespace) -> None:
    # Pool một kết nối: lần sau chắc chắn dùng lại đúng kết nối vừa có app.user_id.
    engine = create_api_engine(url_for_test_db("api"), pool_size=1, max_overflow=0)
    try:
        async with user_session(engine, seeded.a) as s:
            assert await message_ids(s)
        async with engine.connect() as conn:
            setting = await conn.scalar(text("select current_setting('app.user_id', true)"))
            assert setting in (None, "")
            assert await conn.scalar(text("select count(*) from messages")) == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("bad_id", [0, -1, True])
async def test_user_session_rejects_bad_id(bad_id: int) -> None:
    # URL không kết nối được: nếu helper chạm tới DB thì lỗi sẽ là OperationalError.
    engine = create_api_engine("postgresql+psycopg://x:y@127.0.0.1:1/none")
    try:
        with pytest.raises(ValueError):
            async with user_session(engine, bad_id):
                pass
    finally:
        await engine.dispose()


async def test_db_errors_do_not_echo_parameters(seeded: SimpleNamespace) -> None:
    # Tham số bind (vd. subject, nội dung mail) không được nằm trong text của exception.
    engine = create_api_engine(url_for_test_db("api"))
    try:
        with pytest.raises(IntegrityError) as ei:
            async with user_session(engine, seeded.a) as s:
                await s.execute(
                    text(
                        "insert into mail_sources (user_id, kind, account, folder_path) "
                        "values (:u, 'pop', :secret, :p)"
                    ),
                    {"u": seeded.a, "secret": "SECRET-MAIL-SUBJECT-123", "p": TMP_FOLDER},
                )
        assert "SECRET-MAIL-SUBJECT-123" not in str(ei.value)
    finally:
        await engine.dispose()


async def test_user_session_commits_on_success(seeded: SimpleNamespace) -> None:
    engine = create_api_engine(url_for_test_db("api"))
    try:
        async with user_session(engine, seeded.a) as s:
            await s.execute(INSERT_TMP_SOURCE, {"u": seeded.a, "p": TMP_FOLDER})
        async with user_session(engine, seeded.a) as s:
            assert await count_tmp_sources(s) == 1
    finally:
        await engine.dispose()


async def test_user_session_rolls_back_on_error(seeded: SimpleNamespace) -> None:
    engine = create_api_engine(url_for_test_db("api"))
    try:
        with pytest.raises(RuntimeError):
            async with user_session(engine, seeded.a) as s:
                await s.execute(INSERT_TMP_SOURCE, {"u": seeded.a, "p": TMP_FOLDER})
                raise RuntimeError("boom")
        async with user_session(engine, seeded.a) as s:
            assert await count_tmp_sources(s) == 0
    finally:
        await engine.dispose()
