"""mail-stats, review-threads và CLI làm sạch / gom thread (spec 2026-10-17-worker-design.md)."""

import base64
import hashlib
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import cli, processing, review
from app.processing import process_user
from app.review import review_html, stats_report
from tests.dbutil import add_mail, add_project, add_user, as_user, url_for_test_db

SECRET_SUBJECT = "SECRET-SUBJECT <script>alert('x')</script>"
SECRET_FROM = "secret.sender@private.example.jp"
SECRET_BODY = "SECRET-BODY nội dung riêng"
OTHER_SUBJECT = "B-PRIVATE-SUBJECT"


@pytest.fixture
def secrets(owner_engine: Engine, clean: None) -> SimpleNamespace:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        add_mail(conn, a, "s1@x.example", subject=SECRET_SUBJECT, frm=SECRET_FROM, body=SECRET_BODY)
        add_mail(
            conn,
            a,
            "s2@x.example",
            irt="s1@x.example",
            subject="Re: " + SECRET_SUBJECT,
            day=1,
            body="Đã nhận.\n\n--\nSECRET-SIGNATURE\nTEL: 03-0000-0000\n",
        )
        add_mail(conn, b, "o1@x.example", subject=OTHER_SUBJECT, body="B body")
    return SimpleNamespace(a=a, b=b)


async def test_stats_report_has_no_mail_strings(
    worker: AsyncEngine, worker_engine: Engine, secrets: SimpleNamespace
) -> None:
    await process_user(worker, secrets.a)
    with worker_engine.connect() as conn:
        report = stats_report(conn, [secrets.a, secrets.b])
    for secret in ("SECRET", "secret.sender", "private.example", "B-PRIVATE", "Đã nhận"):
        assert secret not in report
    assert "Mail: 2 · đã làm sạch (v1): 2" in report
    assert "cắt chữ ký 1 (50.0%)" in report
    assert "Thread: 1 · 1 mail: 0 · 2–5: 1" in report
    assert "chưa gom thread: 1" in report  # B chưa xử lý


async def test_review_html_escapes_and_shows_only_own_threads(
    worker: AsyncEngine, api_engine: Engine, secrets: SimpleNamespace
) -> None:
    await process_user(worker, secrets.a)
    await process_user(worker, secrets.b)
    with api_engine.begin() as conn:
        as_user(conn, secrets.a)
        html, picked = review_html(conn, secrets.a, sample=100, seed=1)
    assert picked == 1
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert "<script>alert" not in html
    assert OTHER_SUBJECT not in html
    assert "SECRET-SIGNATURE" in html  # phần đã cắt hiện trong mục thu gọn
    assert "http://" not in html and "https://" not in html
    assert 'translate="no"' in html and "notranslate" in html


def test_review_csp_allows_only_page_script() -> None:
    html = review._page([], 0)
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    csp = re.search(r"script-src 'sha256-([^']+)'", html)
    assert script is not None and csp is not None
    digest = base64.b64encode(hashlib.sha256(script.group(1).encode()).digest()).decode()
    assert csp.group(1) == digest
    assert "script-src 'unsafe-inline'" not in html


async def test_review_html_filters_user_even_without_rls(
    worker: AsyncEngine, worker_engine: Engine, secrets: SimpleNamespace
) -> None:
    # Khi có chia sẻ theo dự án, RLS sẽ cho thấy thread người khác: truy vấn tự lọc user_id.
    await process_user(worker, secrets.a)
    await process_user(worker, secrets.b)
    with worker_engine.connect() as conn:  # app_worker: BYPASSRLS
        html, picked = review_html(conn, secrets.a, sample=100)
    assert picked == 1 and OTHER_SUBJECT not in html


def test_review_html_without_user_shows_nothing(
    api_engine: Engine, secrets: SimpleNamespace
) -> None:
    with api_engine.begin() as conn:  # không đặt app.user_id
        html, picked = review_html(conn, secrets.a, sample=100)
    assert picked == 0 and "SECRET" not in html


def test_prune_old_reviews(tmp_path: Path) -> None:
    old, recent, other = (tmp_path / n for n in ("threads-1.html", "threads-2.html", "x.html"))
    for f in (old, recent, other):
        f.write_text("x", encoding="utf-8")
    week_ago = time.time() - 8 * 86400
    os.utime(old, (week_ago, week_ago))
    os.utime(other, (week_ago, week_ago))
    assert review.prune_old_reviews(tmp_path) == 1
    assert (old.exists(), recent.exists(), other.exists()) == (False, True, True)


def use_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("owner", "api", "worker"):
        monkeypatch.setenv(f"DATABASE_URL_{role.upper()}", url_for_test_db(role))


def test_cli_mail_commands(
    secrets: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    owner_engine: Engine,
) -> None:
    use_test_db(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert cli.main(["process-mail", "--email", "a@example.com"]) == 0
    out = capsys.readouterr().out
    assert "làm sạch 2 mail (lỗi 0)" in out and "2 mail → 1 thread" in out

    assert cli.main(["mail-stats"]) == 0
    out = capsys.readouterr().out
    assert f"Người dùng id={secrets.a}" in out and f"Người dùng id={secrets.b}" in out
    assert "SECRET" not in out

    with pytest.raises(SystemExit):  # không còn chọn được nơi ghi file
        cli.main(["review-threads", "--email", "a@example.com", "--out", str(tmp_path / "x")])
    capsys.readouterr()
    assert cli.main(["review-threads", "--email", "a@example.com"]) == 0
    assert "Đã ghi 1 thread" in capsys.readouterr().out
    (written,) = (tmp_path / "PMMailAssistant" / "review").glob("threads-*.html")
    assert "SECRET-BODY" in written.read_text(encoding="utf-8")

    with owner_engine.connect() as conn:  # B không bị xử lý bởi lệnh của A
        assert (
            conn.scalar(
                text("select count(*) from messages where user_id = :b and thread_id is not null"),
                {"b": secrets.b},
            )
            == 0
        )


def poison_thread_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gom thread ra thread_method sai: UPDATE messages lỗi CHECK, DETAIL chứa cả dòng mail."""
    real = processing.build_threads

    def bad(rows: Any, overrides: Any = ()) -> Any:
        result = real(rows, overrides)
        methods: dict[int, Any] = result.method
        for k in methods:
            methods[k] = "guess"
        return result

    monkeypatch.setattr(processing, "build_threads", bad)


async def test_db_error_detail_contains_mail(
    worker: AsyncEngine, secrets: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cơ sở của hai test sau: thông điệp gốc của lỗi DB làm lộ nội dung mail.
    poison_thread_methods(monkeypatch)
    with pytest.raises(DBAPIError) as ei:
        await process_user(worker, secrets.a)
    assert "SECRET-BODY" in str(ei.value)


def test_cli_error_is_sanitized(
    secrets: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_test_db(monkeypatch)
    poison_thread_methods(monkeypatch)
    assert cli.main(["process-mail", "--email", "a@example.com"]) == 1
    captured = capsys.readouterr()
    assert "constraint=ck_messages_thread_method" in captured.err
    assert "sqlstate=23514" in captured.err
    assert "SECRET" not in captured.err + captured.out


async def test_stats_report_counts_project_mail_without_names(
    worker: AsyncEngine, worker_engine: Engine, owner_engine: Engine, secrets: SimpleNamespace
) -> None:
    with owner_engine.begin() as conn:
        add_project(conn, secrets.a, "TÊN-DỰ-ÁN-BÍ-MẬT", filter_from=["private.example.jp"])
        add_project(conn, secrets.a, "Dự án thứ hai", filter_from=["private.example.jp"])
    await process_user(worker, secrets.a)
    with worker_engine.connect() as conn:
        report = stats_report(conn, [secrets.a])
    assert "Dự án: 2 dự án · 1 mail đã gán dự án" in report  # một mail khớp hai dự án: đếm một lần
    assert "TÊN-DỰ-ÁN" not in report and "thứ hai" not in report


def test_cli_assign_projects(
    secrets: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    owner_engine: Engine,
) -> None:
    use_test_db(monkeypatch)
    assert cli.main(["assign-projects", "--email", "a@example.com"]) == 0
    assert f"Người dùng id={secrets.a}: chưa có dự án." in capsys.readouterr().out
    with owner_engine.begin() as conn:
        add_project(conn, secrets.a, "P", filter_from=["private.example.jp"])
        conn.execute(
            text("insert into user_project_state (user_id, rules_version) values (:u, 1)"),
            {"u": secrets.a},
        )
    assert cli.main(["assign-projects", "--email", "a@example.com"]) == 0
    assert f"Người dùng id={secrets.a}: đã xét 2 mail, 1 mail có dự án." in capsys.readouterr().out
