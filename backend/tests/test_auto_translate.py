# ruff: noqa: F811
"""Job dịch tự động (spec 26 mục 4, 5, 8; spec 28: không che). AI giả, dữ liệu giả.

`api` là engine app_api (RLS), `worker` là engine app_worker. Mọi đọc, ghi dữ liệu mail của job đi
qua `api`: test_uses_rls_dictionary chứng minh vì sao.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import auto_translate
from app.ai.client import LlmClient, LlmError, LlmResponse
from app.ai.prompts import TranslationOut
from app.ai.provider import LlmResolver
from app.ai.translate import TranslateError, mail_hash, saved_translations
from app.auto_translate import (
    ELIGIBLE_SQL,
    MAX_RETRY_WAIT_MINUTES,
    RunStats,
    choose_target,
    next_message,
    pending_translate_users,
    translate_next,
)
from app.db.session import user_session
from app.mail.domains import domain_matches, is_inbox_path
from app.processing import process_user
from app.projects import update_project
from app.web_schemas import ProjectInput
from tests.dbutil import (
    T0,
    add_auto_run,
    add_project,
    add_source,
    add_translation,
    add_user,
    set_ai_models,
)
from tests.mlfake import FakeLlm
from tests.test_ai_api import api, llm  # noqa: F401
from tests.test_web_api import put_mail

INBOX_PATH = "ImapMail/imap.example.com/INBOX"
FOLDER_PATH = "ImapMail/imap.example.com/INBOX.sbd/ProjectA"
DOMAIN = "sample-a.example.jp"
CUSTOMER = f"tanaka@{DOMAIN}"
OUT = TranslationOut(subject="Tiêu đề dịch", body="Nội dung dịch", notes=[])


@dataclass
class HookLlm(FakeLlm):
    """AI giả chạy hook ngay trước khi trả lời: giả lập việc xảy ra trong lúc chờ AI."""

    hook: Callable[[], None] = field(default=lambda: None)

    async def create(self, **kwargs: Any) -> LlmResponse:
        self.hook()
        return await super().create(**kwargs)


@pytest.fixture
async def world(owner_engine: Engine, worker: AsyncEngine, clean: None) -> SimpleNamespace:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        set_ai_models(conn, a)
        inbox = add_source(conn, a, INBOX_PATH)
        folder = add_source(conn, a, FOLDER_PATH)
        project = add_project(
            conn, a, "P", customer_domains=[DOMAIN], ai_since=T0, processed_source_id=folder
        )
    return SimpleNamespace(
        a=a, b=b, inbox=inbox, folder=folder, project=project, owner=owner_engine, worker=worker
    )


def link(owner: Engine, user: int, message: int, project: int) -> None:
    with owner.begin() as conn:
        conn.execute(
            text(
                "insert into message_projects (user_id, message_id, project_id) values (:u, :m, :p)"
            ),
            {"u": user, "m": message, "p": project},
        )


def execute(owner: Engine, sql: str, **params: Any) -> None:
    with owner.begin() as conn:
        conn.execute(text(sql), params)


async def put(
    world: SimpleNamespace,
    mid: str,
    *,
    frm: str = CUSTOMER,
    folder: int | None = None,
    day: float | None = 1,
    subject: str = "件名",
    body: str = "本文です。",
    project: int | None | str = "default",
) -> int:
    """Thêm mail cho A (mặc định ở Inbox, thuộc dự án của world), làm sạch và gom thread."""
    with world.owner.begin() as conn:
        m = put_mail(
            conn,
            world.a,
            mid,
            [folder or world.inbox],
            subject=subject,
            body=body,
            frm=frm,
            day=day,
        )
    await process_user(world.worker, world.a)
    target = world.project if project == "default" else project
    if isinstance(target, int):
        link(world.owner, world.a, m, target)
    return m


async def picked(api: AsyncEngine, user: int) -> tuple[int, int] | None:
    async with user_session(api, user) as s:
        return await next_message(s, user)


async def assert_not_selected(api: AsyncEngine, world: SimpleNamespace) -> None:
    assert await picked(api, world.a) is None
    assert await pending_translate_users(world.worker) == []


def run_row(owner: Engine, user: int, message: int) -> Any:
    with owner.connect() as conn:
        return conn.execute(
            text("select * from message_auto_runs where user_id = :u and message_id = :m"),
            {"u": user, "m": message},
        ).first()


def run_count(owner: Engine) -> int:
    with owner.connect() as conn:
        return conn.scalar(text("select count(*) from message_auto_runs")) or 0


def translation_of(owner: Engine, user: int, message: int) -> Any:
    with owner.connect() as conn:
        return conn.execute(
            text("select * from message_translations where user_id = :u and message_id = :m"),
            {"u": user, "m": message},
        ).first()


async def next_run(api: AsyncEngine, llm: LlmClient, user: int) -> RunStats:
    return await translate_next(api, LlmResolver(llm), user)


# --- tiêu chí (spec mục 4) ---


async def test_eligible_full_match(api: AsyncEngine, world: SimpleNamespace) -> None:
    m = await put(world, "m1@x.example")
    assert await picked(api, world.a) == (m, world.inbox)
    assert await pending_translate_users(world.worker) == [world.a]


async def test_skip_not_in_project(api: AsyncEngine, world: SimpleNamespace) -> None:
    await put(world, "m1@x.example", project=None)
    await assert_not_selected(api, world)


async def test_skip_domain_mismatch(api: AsyncEngine, world: SimpleNamespace) -> None:
    await put(world, "m1@x.example", frm="sato@other.example.jp")
    await assert_not_selected(api, world)


async def test_skip_subfolder_of_inbox(api: AsyncEngine, world: SimpleNamespace) -> None:
    await put(world, "m1@x.example", folder=world.folder)
    await assert_not_selected(api, world)


async def test_skip_before_ai_since(api: AsyncEngine, world: SimpleNamespace) -> None:
    await put(world, "m1@x.example", day=-1)
    await assert_not_selected(api, world)


async def test_skip_sent_at_null(api: AsyncEngine, world: SimpleNamespace) -> None:
    await put(world, "m1@x.example", day=None)
    await assert_not_selected(api, world)


async def test_skip_ownership_deleted(api: AsyncEngine, world: SimpleNamespace) -> None:
    await put(world, "m1@x.example")
    execute(world.owner, "update message_ownership set is_deleted = true")
    await assert_not_selected(api, world)


@pytest.mark.parametrize("column", ["body_clean", "thread_id"])
async def test_skip_not_cleaned(api: AsyncEngine, world: SimpleNamespace, column: str) -> None:
    await put(world, "m1@x.example")
    execute(world.owner, f"update messages set {column} = null")
    await assert_not_selected(api, world)


@pytest.mark.parametrize("status", ["done", "failed"])
async def test_skip_done_and_failed(api: AsyncEngine, world: SimpleNamespace, status: str) -> None:
    m = await put(world, "m1@x.example")
    with world.owner.begin() as conn:
        add_auto_run(conn, world.a, m, status)
    await assert_not_selected(api, world)


async def test_skip_retry_until_due(api: AsyncEngine, world: SimpleNamespace) -> None:
    m = await put(world, "m1@x.example")
    with world.owner.begin() as conn:
        add_auto_run(
            conn, world.a, m, "retry", next_attempt_at=datetime.now(UTC) + timedelta(minutes=5)
        )
    await assert_not_selected(api, world)
    execute(
        world.owner,
        "update message_auto_runs set next_attempt_at = :at",
        at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await picked(api, world.a) == (m, world.inbox)


async def test_domain_of_other_project_not_counted(
    api: AsyncEngine, world: SimpleNamespace
) -> None:
    """Domain khai ở Y không làm mail của X thành mail khách."""
    with world.owner.begin() as conn:
        x = add_project(conn, world.a, "X", customer_domains=["x.example.jp"], ai_since=T0)
    await put(world, "m1@x.example", project=x)
    await assert_not_selected(api, world)


async def test_ai_since_of_other_project_not_counted(
    api: AsyncEngine, world: SimpleNamespace
) -> None:
    """Mốc xét theo đúng dự án có domain khớp, không lấy mốc sớm hơn của dự án khác."""
    with world.owner.begin() as conn:
        late = add_project(
            conn, world.a, "L", customer_domains=[DOMAIN], ai_since=T0 + timedelta(days=9)
        )
        early = add_project(conn, world.a, "E", customer_domains=["e.example.jp"], ai_since=T0)
    m = await put(world, "m1@x.example", project=late)
    link(world.owner, world.a, m, early)
    await assert_not_selected(api, world)


async def test_subdomain_matches_python_and_sql(api: AsyncEngine, world: SimpleNamespace) -> None:
    addrs = [
        CUSTOMER,
        f"tanaka@mail.{DOMAIN}",
        f"Tanaka@Mail.{DOMAIN.upper()}",
        f"tanaka@x{DOMAIN}",
        f"tanaka@{DOMAIN}.evil.example",
        f'"a@b"@{DOMAIN}',
        f"{DOMAIN}",  # không có @
        "tanaka@other.example.jp",
    ]
    ids = [await put(world, f"m{i}@x.example", frm=a) for i, a in enumerate(addrs)]
    expected = {m for m, a in zip(ids, addrs, strict=True) if domain_matches(a, [DOMAIN])}
    assert len(expected) == 4
    async with user_session(api, world.a) as s:
        got = set(
            await s.scalars(
                text(f"select m.id from messages m where {ELIGIBLE_SQL}"), {"u": world.a}
            )
        )
    assert got == expected


@pytest.mark.parametrize("addr", ["tanaka@", "tanaka@example.jp."])
async def test_empty_domain_value_matches_nothing(
    api: AsyncEngine, world: SimpleNamespace, addr: str
) -> None:
    """DB không chặn '' trong customer_domains: nếu lọt thì host rỗng hoặc đuôi "." sẽ khớp."""
    execute(world.owner, "update projects set customer_domains = '{\"\"}'")
    await put(world, "m1@x.example", frm=addr)
    await assert_not_selected(api, world)


async def test_inbox_of_any_account(api: AsyncEngine, world: SimpleNamespace) -> None:
    paths = ["Mail/Local Folders/Inbox", "ImapMail/imap2.example.com/inbox"]
    with world.owner.begin() as conn:
        sources = [add_source(conn, world.a, p, account=p) for p in paths]
    for i, source in enumerate(sources):
        assert is_inbox_path(paths[i])
        m = await put(world, f"m{i}@x.example", folder=source, day=1 + i)
        assert await picked(api, world.a) == (m, source)
        execute(world.owner, "delete from message_projects")


async def test_pending_users_filters_each_user(api: AsyncEngine, world: SimpleNamespace) -> None:
    """Dự án của B không làm mail của A đủ tiêu chí, và ngược lại (role worker bỏ qua RLS)."""
    with world.owner.begin() as conn:
        add_project(conn, world.b, "PB", customer_domains=[DOMAIN], ai_since=T0)
        s_b = add_source(conn, world.b, INBOX_PATH)
        put_mail(conn, world.b, "b1@x.example", [s_b], subject="s", body="b", frm=CUSTOMER, day=1)
    await process_user(world.worker, world.b)
    assert await pending_translate_users(world.worker) == []
    await put(world, "m1@x.example")
    assert await pending_translate_users(world.worker) == [world.a]


async def test_rls_next_message_sees_only_own_mail(
    api: AsyncEngine, world: SimpleNamespace
) -> None:
    await put(world, "m1@x.example")
    assert await picked(api, world.b) is None


# --- một lần chạy (spec mục 5) ---


async def test_translates_saves_and_moves(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    m = await put(world, "m1@x.example")
    llm.responses = [OUT]
    stats = await next_run(api, llm, world.a)
    assert stats.handled
    saved = translation_of(world.owner, world.a, m)
    assert saved.translated_body == "Nội dung dịch"
    row = run_row(world.owner, world.a, m)
    assert (
        row.status,
        row.target_source_id,
        row.from_source_id,
        row.move_note,
        row.error_code,
    ) == (
        "done",
        world.folder,
        world.inbox,
        None,
        None,
    )
    assert row.attempts == 1
    # Mail chỉ được xử lý một lần dù ownership tới Inbox vẫn còn.
    assert await picked(api, world.a) is None


async def test_no_folder_note(api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace) -> None:
    execute(world.owner, "update projects set processed_source_id = null")
    m = await put(world, "m1@x.example")
    llm.responses = [OUT]
    await next_run(api, llm, world.a)
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.target_source_id, row.move_note) == ("done", None, "no_folder")
    assert translation_of(world.owner, world.a, m) is not None


async def save_folder(api: AsyncEngine, user: int, project: int, folder: int | None) -> None:
    """Lưu dự án như nút Lưu trên web: mọi trường giữ nguyên, chỉ đổi folder xử lý."""
    req = ProjectInput(name="P", customer_domains=[DOMAIN], processed_source_id=folder)
    async with user_session(api, user) as s:
        await update_project(s, user, project, req)


@pytest.fixture
async def no_folder_mail(api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace) -> int:
    """Đúng thứ tự tự nhiên: lưu domain, job chạy khi chưa chọn folder, mail thành no_folder."""
    execute(world.owner, "update projects set processed_source_id = null")
    m = await put(world, "m1@x.example")
    llm.responses = [OUT]
    await next_run(api, llm, world.a)
    assert run_row(world.owner, world.a, m).move_note == "no_folder"
    return m


async def test_choosing_folder_later_moves_no_folder_mail(
    api: AsyncEngine, world: SimpleNamespace, no_folder_mail: int
) -> None:
    await save_folder(api, world.a, world.project, world.folder)
    row = run_row(world.owner, world.a, no_folder_mail)
    assert (row.status, row.target_source_id, row.move_note) == ("done", world.folder, None)
    assert row.attempts == 1 and row.from_source_id == world.inbox


async def test_moved_back_mail_not_moved_again_by_folder_change(
    api: AsyncEngine, world: SimpleNamespace, no_folder_mail: int
) -> None:
    """move-back xóa note: đổi folder sau đó không đảo lựa chọn của người dùng."""
    await save_folder(api, world.a, world.project, world.folder)
    execute(world.owner, "update message_auto_runs set target_source_id = null, move_note = null")
    with world.owner.begin() as conn:
        other_folder = add_source(conn, world.a, f"{INBOX_PATH}.sbd/ProjectB")
    await save_folder(api, world.a, world.project, other_folder)
    row = run_row(world.owner, world.a, no_folder_mail)
    assert (row.target_source_id, row.move_note) == (None, None)


async def test_saving_without_folder_change_touches_nothing(
    api: AsyncEngine, world: SimpleNamespace, no_folder_mail: int
) -> None:
    before = run_row(world.owner, world.a, no_folder_mail).updated_at
    await save_folder(api, world.a, world.project, None)
    row = run_row(world.owner, world.a, no_folder_mail)
    assert (row.target_source_id, row.move_note) == (None, "no_folder")
    assert row.updated_at == before


async def test_folder_of_project_with_other_domain_keeps_no_folder(
    api: AsyncEngine, world: SimpleNamespace, no_folder_mail: int
) -> None:
    """Xét lại bằng chính choose_target: dự án domain không khớp mail không quyết định folder."""
    with world.owner.begin() as conn:
        other_folder = add_source(conn, world.a, f"{INBOX_PATH}.sbd/ProjectB")
        other = add_project(conn, world.a, "Q", customer_domains=["q.example.jp"])
    link(world.owner, world.a, no_folder_mail, other)
    req = ProjectInput(
        name="Q", customer_domains=["q.example.jp"], processed_source_id=other_folder
    )
    async with user_session(api, world.a) as s:
        await update_project(s, world.a, other, req)
    row = run_row(world.owner, world.a, no_folder_mail)
    assert (row.target_source_id, row.move_note) == (None, "no_folder")


async def test_folder_change_only_retargets_no_folder_rows_of_that_project(
    api: AsyncEngine, world: SimpleNamespace, no_folder_mail: int
) -> None:
    ambiguous = await put(world, "m2@x.example")
    failed = await put(world, "m3@x.example")
    outside = await put(world, "m4@x.example", project=None)
    with world.owner.begin() as conn:
        add_auto_run(conn, world.a, ambiguous, move_note="ambiguous")
        add_auto_run(conn, world.a, failed, "failed", move_note="no_folder")
        add_auto_run(conn, world.a, outside, move_note="no_folder")
    await save_folder(api, world.a, world.project, world.folder)
    assert run_row(world.owner, world.a, no_folder_mail).target_source_id == world.folder
    for m, note in ((ambiguous, "ambiguous"), (failed, "no_folder"), (outside, "no_folder")):
        row = run_row(world.owner, world.a, m)
        assert (row.target_source_id, row.move_note) == (None, note)


async def test_two_folders_ambiguous_stays(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    with world.owner.begin() as conn:
        other_folder = add_source(conn, world.a, f"{INBOX_PATH}.sbd/ProjectB")
        other = add_project(
            conn,
            world.a,
            "Q",
            customer_domains=[DOMAIN],
            ai_since=T0,
            processed_source_id=other_folder,
        )
    m = await put(world, "m1@x.example")
    link(world.owner, world.a, m, other)
    async with user_session(api, world.a) as s:
        assert await choose_target(s, world.a, m) == (None, "ambiguous")
    llm.responses = [OUT]
    await next_run(api, llm, world.a)
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.target_source_id, row.move_note) == ("done", None, "ambiguous")


async def test_two_projects_same_folder_moves(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    with world.owner.begin() as conn:
        other = add_project(
            conn,
            world.a,
            "Q",
            customer_domains=[DOMAIN],
            ai_since=T0,
            processed_source_id=world.folder,
        )
    m = await put(world, "m1@x.example")
    link(world.owner, world.a, m, other)
    llm.responses = [OUT]
    await next_run(api, llm, world.a)
    assert run_row(world.owner, world.a, m).target_source_id == world.folder


async def test_project_without_matching_domain_has_no_say_in_folder(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """Chọn folder chỉ xét dự án có domain khớp (spec mục 8)."""
    with world.owner.begin() as conn:
        other_folder = add_source(conn, world.a, f"{INBOX_PATH}.sbd/ProjectB")
        other = add_project(
            conn, world.a, "Q", customer_domains=["q.example.jp"], processed_source_id=other_folder
        )
    m = await put(world, "m1@x.example")
    link(world.owner, world.a, m, other)
    async with user_session(api, world.a) as s:
        assert await choose_target(s, world.a, m) == (world.folder, None)


async def test_existing_translation_no_ai_call(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    m = await put(world, "m1@x.example")
    with world.owner.begin() as conn:
        add_translation(conn, world.a, m, translated_body="Bản dịch tay")
    await next_run(api, llm, world.a)
    assert llm.requests == []
    assert translation_of(world.owner, world.a, m).translated_body == "Bản dịch tay"
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.target_source_id) == ("done", world.folder)


async def test_only_one_mail_per_call_and_more_flag(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    newer = await put(world, "new@x.example", day=2)
    older = await put(world, "old@x.example", day=1)
    llm.responses = [OUT, OUT]
    assert await next_run(api, llm, world.a) == RunStats(handled=True, more=True)
    assert len(llm.requests) == 1
    assert run_row(world.owner, world.a, older) is not None  # mail cũ nhất trước
    assert run_row(world.owner, world.a, newer) is None
    assert await next_run(api, llm, world.a) == RunStats(handled=True, more=False)
    assert await next_run(api, llm, world.a) == RunStats(handled=False, more=False)
    assert len(llm.requests) == 2


async def test_needs_translate_model_only(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    m = await put(world, "m1@x.example")
    execute(world.owner, "delete from user_ai_settings where task <> 'translate'")
    execute(world.owner, "update user_ai_settings set model = 'gw/model-x'")
    llm.responses = [OUT]
    stats = await translate_next(api, LlmResolver(llm), world.a)
    assert stats.handled
    assert llm.requests[0]["model"] == "gw/model-x"
    assert run_row(world.owner, world.a, m).status == "done"


# --- lỗi ---


@pytest.mark.parametrize("case", ["ai_unavailable", "no_ai", "no_model", "bad_key"])
async def test_transient_errors_stop_without_row(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, case: str
) -> None:
    m = await put(world, "m1@x.example")
    resolver: LlmResolver = LlmResolver(llm)
    if case == "ai_unavailable":
        llm.responses = [LlmError("ai_unavailable", 503, "")]
    elif case == "no_ai":
        resolver = LlmResolver()
    elif case == "no_model":
        execute(world.owner, "delete from user_ai_settings")
    else:

        class BadKey(LlmResolver):
            async def for_user(self, engine: AsyncEngine, user_id: int) -> LlmClient | None:
                raise LlmError("ai_auth_failed", 503, "")

        resolver = BadKey()
    assert await translate_next(api, resolver, world.a) == RunStats(False, False)
    assert run_row(world.owner, world.a, m) is None
    assert translation_of(world.owner, world.a, m) is None
    assert (await picked(api, world.a)) == (m, world.inbox)  # lần quét sau làm lại


async def test_other_llm_errors_are_transient(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    m = await put(world, "m1@x.example")
    llm.responses = [LlmError("ai_auth_failed", 503, "")]
    assert await next_run(api, llm, world.a) == RunStats(False, False)
    assert run_row(world.owner, world.a, m) is None


async def test_bad_request_never_fails_and_does_not_block_next_mail(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """ai_bad_request có thể do một mail (quá dài) hoặc do cấu hình (sai tên model): mail chờ, mail
    sau vẫn chạy, và không bao giờ thành failed để sửa cấu hình xong là mail chạy lại được."""
    blocked = await put(world, "old@x.example", day=1)
    after = await put(world, "new@x.example", day=2)
    llm.responses = [LlmError("ai_bad_request", 502, ""), OUT]
    before = datetime.now(UTC)
    assert await next_run(api, llm, world.a) == RunStats(True, True)
    row = run_row(world.owner, world.a, blocked)
    assert (row.status, row.attempts, row.error_code) == ("retry", 1, "ai_bad_request")
    assert timedelta(minutes=10) <= row.next_attempt_at - before < timedelta(minutes=11)

    assert await picked(api, world.a) == (after, world.inbox)
    await next_run(api, llm, world.a)
    assert run_row(world.owner, world.a, after).status == "done"

    for attempt in range(2, 12):
        execute(world.owner, "update message_auto_runs set next_attempt_at = now()")
        llm.responses = [LlmError("ai_bad_request", 502, "")]
        before = datetime.now(UTC)
        assert await next_run(api, llm, world.a) == RunStats(True, True)
        row = run_row(world.owner, world.a, blocked)
        assert (row.status, row.attempts, row.error_code) == ("retry", attempt, "ai_bad_request")
        minutes = min(attempt * 10, MAX_RETRY_WAIT_MINUTES)
        assert timedelta(minutes=minutes) <= row.next_attempt_at - before
        assert row.next_attempt_at - before < timedelta(minutes=minutes, seconds=30)
    assert MAX_RETRY_WAIT_MINUTES == 60

    execute(world.owner, "update message_auto_runs set next_attempt_at = now()")
    llm.responses = [OUT]
    await next_run(api, llm, world.a)
    row = run_row(world.owner, world.a, blocked)
    assert (row.status, row.error_code, row.target_source_id) == ("done", None, world.folder)


async def test_bad_request_from_resolver_stops_without_row(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """Địa chỉ nhà cung cấp bị chặn: lỗi cấu hình, chưa chọn mail nào nên không có gì để ghi."""
    m = await put(world, "m1@x.example")

    class BlockedHost(LlmResolver):
        async def for_user(self, engine: AsyncEngine, user_id: int) -> LlmClient | None:
            raise LlmError("ai_bad_request", 502, "")

    assert await translate_next(api, BlockedHost(), world.a) == RunStats(False, False)
    assert run_row(world.owner, world.a, m) is None


async def test_per_mail_error_retries_then_failed(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    m = await put(world, "m1@x.example")
    llm.responses = [LlmError("ai_bad_output", 502, "")] * 3
    for attempt, minutes in ((1, 10), (2, 20)):
        before = datetime.now(UTC)
        assert await next_run(api, llm, world.a) == RunStats(True, True)
        row = run_row(world.owner, world.a, m)
        assert (row.status, row.attempts, row.error_code) == ("retry", attempt, "ai_bad_output")
        assert row.from_source_id == world.inbox
        assert row.target_source_id is None
        wait = row.next_attempt_at - before
        assert timedelta(minutes=minutes) <= wait < timedelta(minutes=minutes, seconds=30)
        # Còn trong thời gian chờ thì không chọn lại.
        assert await next_run(api, llm, world.a) == RunStats(False, False)
        execute(world.owner, "update message_auto_runs set next_attempt_at = now()")
    assert await next_run(api, llm, world.a) == RunStats(True, True)
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.attempts, row.next_attempt_at) == ("failed", 3, None)
    assert row.target_source_id is None  # mail ở lại Inbox
    assert translation_of(world.owner, world.a, m) is None
    assert await picked(api, world.a) is None


async def test_retry_then_success_clears_error(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    m = await put(world, "m1@x.example")
    llm.responses = [LlmError("ai_truncated", 502, ""), OUT]
    await next_run(api, llm, world.a)
    execute(world.owner, "update message_auto_runs set next_attempt_at = now()")
    await next_run(api, llm, world.a)
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.attempts, row.error_code, row.next_attempt_at) == (
        "done",
        2,
        None,
        None,
    )
    assert row.target_source_id == world.folder


async def test_retry_row_with_mask_unstable_runs_again(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """Dòng retry cũ của lỗi mask_unstable (đã hết giờ chờ) được chạy lại và xong như mọi mail."""
    m = await put(world, "m1@x.example")
    with world.owner.begin() as conn:
        add_auto_run(
            conn,
            world.a,
            m,
            "retry",
            error_code="mask_unstable",
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    llm.responses = [OUT]
    assert await next_run(api, llm, world.a) == RunStats(True, False)
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.attempts, row.error_code) == ("done", 2, None)
    assert row.target_source_id == world.folder
    assert translation_of(world.owner, world.a, m).translated_body == "Nội dung dịch"


async def test_unexpected_error_does_not_block_queue(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lỗi lạ khi xử lý một mail: ghi lỗi theo mail, mail sau vẫn chạy. Không phân loại thì mail cũ
    nhất làm job lỗi mãi và chặn cả hàng đợi."""
    stuck = await put(world, "stuck@x.example", day=1, subject="SECRET-SUBJECT")
    after = await put(world, "after@x.example", day=2)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("SECRET-SUBJECT")

    monkeypatch.setattr(auto_translate, "produce_translation", boom)
    with caplog.at_level(logging.INFO):
        assert await next_run(api, llm, world.a) == RunStats(True, True)
    row = run_row(world.owner, world.a, stuck)
    assert (row.status, row.attempts, row.error_code) == ("retry", 1, "translate_failed")
    assert llm.requests == []
    assert "ValueError" in caplog.text
    assert "SECRET" not in caplog.text

    assert await picked(api, world.a) == (after, world.inbox)


async def test_old_prompt_version_marks_stale_but_not_rerun(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """TRANSLATE_PROMPT_VERSION lên 2: bản dịch version 1 hiện "cũ", job không gọi AI lại."""
    m = await put(world, "m1@x.example", subject="件名", body="本文です。")
    with world.owner.begin() as conn:
        add_translation(
            conn,
            world.a,
            m,
            translated_body="Bản dịch cũ",
            source_hash=mail_hash("件名", "本文です。"),
            prompt_version=1,
        )
    async with user_session(api, world.a) as s:
        assert (await saved_translations(s, world.a, [m]))[m].stale is True
    assert (await next_run(api, llm, world.a)).handled
    assert llm.requests == []
    assert translation_of(world.owner, world.a, m).translated_body == "Bản dịch cũ"
    assert run_row(world.owner, world.a, m).status == "done"
    async with user_session(api, world.a) as s:
        assert (await saved_translations(s, world.a, [m]))[m].stale is True


@pytest.mark.parametrize(
    "exc",
    [
        OperationalError("x", {}, Exception("x")),
        TimeoutError("x"),
        httpx.ReadTimeout("x"),
        httpx.ConnectError("x"),
    ],
    ids=["db", "os_timeout", "httpx_timeout", "httpx_connect"],
)
async def test_infrastructure_error_propagates_without_row(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """DB hoặc mạng hỏng không phải lỗi của mail: không tốn lượt thử, job báo lỗi, quét sau làm."""
    m = await put(world, "m1@x.example")

    async def down(*args: Any, **kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr(auto_translate, "produce_translation", down)
    with pytest.raises(type(exc)):
        await next_run(api, llm, world.a)
    assert run_row(world.owner, world.a, m) is None


async def test_empty_mail_done_and_moved(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """Review Focus 1: chỉ có file đính kèm, không gọi AI, không lặp mãi."""
    m = await put(world, "m1@x.example", subject="", body="")
    assert (await next_run(api, llm, world.a)).handled
    assert llm.requests == []
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.error_code, row.target_source_id) == (
        "done",
        "nothing_to_translate",
        world.folder,
    )
    assert translation_of(world.owner, world.a, m) is None
    assert await picked(api, world.a) is None


async def test_message_deleted_during_ai_call(api: AsyncEngine, world: SimpleNamespace) -> None:
    """Review Focus 2: xóa mail trong lúc chờ AI thì không lỗi, không ghi gì."""
    m = await put(world, "m1@x.example")
    other = await put(world, "m2@x.example", day=2)
    llm = HookLlm(
        responses=[OUT, OUT],
        hook=lambda: execute(world.owner, "delete from messages where id = :m", m=m),
    )
    assert await next_run(api, llm, world.a) == RunStats(True, True)
    assert run_count(world.owner) == 0
    assert translation_of(world.owner, world.a, m) is None
    assert await picked(api, world.a) == (other, world.inbox)


async def test_message_deleted_before_read_goes_on(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await put(world, "m1@x.example")

    async def gone(*args: Any, **kwargs: Any) -> Any:
        raise TranslateError(404, "message_not_found", "x")

    monkeypatch.setattr(auto_translate, "produce_translation", gone)
    assert await next_run(api, llm, world.a) == RunStats(True, True)
    assert run_count(world.owner) == 0


async def test_message_deleted_after_recheck_no_fk_error(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mail mất ngay trước lúc ghi dòng run: FK 23503 được bỏ qua."""
    m = await put(world, "m1@x.example")
    with world.owner.begin() as conn:
        add_translation(conn, world.a, m)
    real = auto_translate.choose_target

    async def deleting(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        execute(world.owner, "delete from messages where id = :m", m=m)
        return result

    monkeypatch.setattr(auto_translate, "choose_target", deleting)
    assert await next_run(api, llm, world.a) == RunStats(True, True)
    assert run_count(world.owner) == 0


async def test_criteria_rechecked_before_write(api: AsyncEngine, world: SimpleNamespace) -> None:
    """Review Focus 2: dự án bị xóa trong lúc gọi AI thì không chuyển folder, không ghi gì."""
    m = await put(world, "m1@x.example")
    llm = HookLlm(responses=[OUT], hook=lambda: execute(world.owner, "delete from projects"))
    assert await next_run(api, llm, world.a) == RunStats(True, True)
    assert run_row(world.owner, world.a, m) is None
    assert translation_of(world.owner, world.a, m) is None


async def test_race_with_manual_translate(api: AsyncEngine, world: SimpleNamespace) -> None:
    """Review Focus 4: bấm Dịch đúng lúc job đang dịch cùng mail."""
    m = await put(world, "m1@x.example")

    def manual() -> None:
        with world.owner.begin() as conn:
            add_translation(conn, world.a, m)

    llm = HookLlm(responses=[OUT], hook=manual)
    assert (await next_run(api, llm, world.a)).handled
    with world.owner.connect() as conn:
        rows = conn.execute(text("select status, target_source_id from message_auto_runs")).all()
        saved = conn.scalar(text("select count(*) from message_translations"))
    assert [tuple(r) for r in rows] == [("done", world.folder)]
    assert saved == 1
    assert await picked(api, world.a) is None


async def test_done_row_is_never_overwritten(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hai job chạy đua: người dùng đã chuyển mail lại Inbox sau lần xong đầu thì giữ nguyên."""
    m = await put(world, "m1@x.example")
    with world.owner.begin() as conn:
        add_translation(conn, world.a, m)
    real = auto_translate.choose_target

    async def finished_by_other_job(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        with world.owner.begin() as conn:
            add_auto_run(conn, world.a, m, "done", attempts=1)
        return result

    monkeypatch.setattr(auto_translate, "choose_target", finished_by_other_job)
    await next_run(api, llm, world.a)
    row = run_row(world.owner, world.a, m)
    assert (row.status, row.attempts, row.target_source_id) == ("done", 1, None)


async def test_request_has_only_own_mail(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    """Job đọc qua RLS: người gửi và nội dung mail của B không vào request của A."""
    with world.owner.begin() as conn:
        s_b = add_source(conn, world.b, INBOX_PATH)
        put_mail(
            conn,
            world.b,
            "b1@x.example",
            [s_b],
            subject="SECRET-OF-B",
            body="SECRET-OF-B",
            frm="kuroda@b.example.jp",
            from_name="Kuroda Zenji",
            day=1,
        )
    await process_user(world.worker, world.b)
    await put(world, "m1@x.example", body="Liên hệ Kuroda Zenji nhé.")
    llm.responses = [OUT]
    await next_run(api, llm, world.a)
    sent = llm.requests[0]["user"]
    assert "Kuroda Zenji" in sent  # tên trong chính mail của A đi nguyên văn
    assert "SECRET-OF-B" not in sent and "kuroda@b.example.jp" not in sent


async def test_log_has_no_mail_content(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await put(world, "m1@x.example", subject="SECRET-SUBJECT", body="SECRET-BODY")
    llm.responses = [TranslationOut(subject="SECRET-OUT", body="SECRET-OUT", notes=[])]
    with caplog.at_level(logging.DEBUG):
        await next_run(api, llm, world.a)
    assert "SECRET" not in caplog.text
    assert DOMAIN not in caplog.text
    assert "auto_translate user=" in caplog.text
