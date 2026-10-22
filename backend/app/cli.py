"""CLI quản trị.

Người dùng và token collector (role owner):

    uv run --directory backend python -m app.cli create-user --email pm@example.com --name "PM"
    uv run --directory backend python -m app.cli create-token --email pm@example.com
    uv run --directory backend python -m app.cli revoke-token --email pm@example.com

Token chỉ in ra một lần; DB chỉ lưu SHA-256 của token.

Mật khẩu đăng nhập web (role owner; spec 2026-10-18-web-login-design.md mục 6):

    ... python -m app.cli set-password --email pm@example.com [--password-stdin]

Đặt mật khẩu mới thì mọi phiên web cũ của người đó bị thu hồi.

Làm sạch / gom thread (spec 2026-10-17-worker-design.md mục 5):

    ... python -m app.cli process-mail --email pm@example.com [--rethread]   # role app_worker
    ... python -m app.cli mail-stats [--email pm@example.com]                 # chỉ số liệu tổng hợp
    ... python -m app.cli review-threads --email pm@example.com [--sample 100] [--seed N]

Embedding (spec 2026-10-19-mail-embedding-design.md mục 5; cần ml-service đang chạy):

    ... python -m app.cli embed-mail --email pm@example.com [--limit N]       # role app_worker

review-threads ghi file HTML CÓ NỘI DUNG MAIL cho người dùng tự kiểm tra (đọc bằng app_api + RLS),
chỉ vào %LOCALAPPDATA%\\PMMailAssistant\\review\\. Lỗi của các lệnh mail chỉ in mô tả an toàn.
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Coroutine
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.auth import hash_token, new_token
from app.config import database_url
from app.db.session import user_connection
from app.embedding import EMBED_BATCH, count_pending, embed_batch
from app.ml_client import EmbeddingModelMismatch, HttpMlClient, MlUnavailable
from app.processing import ProcessResult, create_worker_engine, process_user
from app.review import (
    REVIEW_KEEP_DAYS,
    default_review_path,
    prune_old_reviews,
    review_html,
    stats_report,
)
from app.safe_errors import describe
from app.web_auth import hash_password, password_problem

MAIL_COMMANDS = ("process-mail", "mail-stats", "review-threads", "embed-mail")


def create_token(conn: Connection, user_id: int, label: str = "collector") -> str:
    token = new_token()
    conn.execute(
        text("insert into api_tokens (user_id, token_hash, label) values (:u, :h, :l)"),
        {"u": user_id, "h": hash_token(token), "l": label},
    )
    return token


def create_user(
    conn: Connection, email: str, name: str, role: str = "member", label: str = "collector"
) -> tuple[int, str]:
    user_id: int = conn.scalar(
        text("insert into users (email, display_name, role) values (:e, :n, :r) returning id"),
        {"e": email.strip().lower(), "n": name, "r": role},
    )
    return user_id, create_token(conn, user_id, label)


def revoke_tokens(conn: Connection, user_id: int) -> int:
    result = conn.execute(
        text("update api_tokens set revoked_at = now() where user_id = :u and revoked_at is null"),
        {"u": user_id},
    )
    return result.rowcount


def set_password(conn: Connection, user_id: int, password: str) -> int:
    """Ghi mật khẩu web (ghi đè), thu hồi mọi phiên web của người dùng. Trả số phiên đã thu hồi."""
    conn.execute(
        text(
            "insert into user_passwords (user_id, password_hash) values (:u, :h) "
            "on conflict (user_id) do update set password_hash = excluded.password_hash, "
            "updated_at = now()"
        ),
        {"u": user_id, "h": hash_password(password)},
    )
    result = conn.execute(
        text(
            "update web_sessions set revoked_at = now() where user_id = :u and revoked_at is null"
        ),
        {"u": user_id},
    )
    return result.rowcount


def _read_stdin_line() -> str:
    """Một dòng từ stdin, giải mã UTF-8, bỏ BOM ở đầu.

    PowerShell có thể thêm BOM khi pipe chuỗi sang chương trình khác (đã gặp: mật khẩu lưu kèm BOM,
    đăng nhập không bao giờ khớp). Đọc byte để không phụ thuộc codepage của console.
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:  # stdin đã là text (test)
        line = sys.stdin.readline()
    else:
        try:
            line = buffer.readline().decode("utf-8")
        except UnicodeDecodeError:
            print("Mật khẩu từ stdin không phải UTF-8.", file=sys.stderr)
            raise SystemExit(2) from None
    return line.removeprefix("﻿").rstrip("\r\n")


def read_new_password(from_stdin: bool) -> str:
    """Mật khẩu từ stdin (một dòng) hoặc hỏi hai lần bằng getpass. Không hợp lệ → SystemExit(2)."""
    if from_stdin:
        password = _read_stdin_line()
    else:
        password = getpass.getpass("Mật khẩu mới: ")
        if getpass.getpass("Nhập lại: ") != password:
            print("Hai lần nhập không khớp.", file=sys.stderr)
            raise SystemExit(2)
    problem = password_problem(password)
    if problem:
        print(problem, file=sys.stderr)
        raise SystemExit(2)
    return password


def _set_password_command(email: str, from_stdin: bool) -> int:
    engine = create_engine(database_url("owner"), hide_parameters=True)
    try:
        # Kiểm tra email trước khi hỏi mật khẩu (không bắt gõ mật khẩu hai lần cho email sai).
        with engine.connect() as conn:
            user_id = _user_id(conn, email)
        password = read_new_password(from_stdin)
        with engine.begin() as conn:
            n = set_password(conn, user_id, password)
    finally:
        engine.dispose()
    print(f"Đã đặt mật khẩu web cho {email.strip().lower()}. Đã thu hồi {n} phiên đăng nhập cũ.")
    print("Đăng nhập: chạy API (python -m app.run), mở http://127.0.0.1:8000")
    return 0


def _user_id(conn: Connection, email: str) -> int:
    user_id = conn.scalar(
        text("select id from users where email = :e"), {"e": email.strip().lower()}
    )
    if user_id is None:
        raise SystemExit(f"Không có người dùng {email}")
    return user_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-user", help="tạo người dùng và token")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", default="member", choices=["admin", "pm", "member"])
    p = sub.add_parser("create-token", help="tạo thêm token cho người dùng")
    p.add_argument("--email", required=True)
    p.add_argument("--label", default="collector")
    p = sub.add_parser("revoke-token", help="thu hồi mọi token của người dùng")
    p.add_argument("--email", required=True)
    p = sub.add_parser("set-password", help="đặt mật khẩu đăng nhập web (thu hồi phiên web cũ)")
    p.add_argument("--email", required=True)
    p.add_argument("--password-stdin", action="store_true", help="đọc mật khẩu từ một dòng stdin")
    p = sub.add_parser("process-mail", help="làm sạch + gom thread ngay, không cần worker")
    p.add_argument("--email", required=True)
    p.add_argument("--rethread", action="store_true", help="gom lại dù không có việc tồn")
    p = sub.add_parser("mail-stats", help="số liệu tổng hợp làm sạch / gom thread / embedding")
    p.add_argument("--email")
    p = sub.add_parser("embed-mail", help="embed ngay mail chờ embed (cần ml-service đang chạy)")
    p.add_argument("--email", required=True)
    p.add_argument("--limit", type=int, default=0, help="tối đa N mail mới nhất (0: tất cả)")
    p = sub.add_parser(
        "review-threads", help="xuất HTML các thread ngẫu nhiên để tự kiểm tra (có nội dung mail)"
    )
    p.add_argument("--email", required=True)
    p.add_argument("--sample", type=int, default=100)
    p.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    if args.cmd in MAIL_COMMANDS:
        try:
            return _mail_command(args)
        except Exception as e:
            # Không in thông điệp gốc: lỗi DB có DETAIL chứa dòng dữ liệu (nội dung mail).
            print(f"Lỗi: {describe(e)}", file=sys.stderr)
            return 1

    if args.cmd == "set-password":
        return _set_password_command(args.email, args.password_stdin)

    engine = create_engine(database_url("owner"), hide_parameters=True)
    try:
        with engine.begin() as conn:
            if args.cmd == "create-user":
                user_id, token = create_user(conn, args.email, args.name, args.role)
                print(f"Đã tạo người dùng id={user_id}.")
                print(f"Token (chỉ hiện một lần): {token}")
            elif args.cmd == "create-token":
                token = create_token(conn, _user_id(conn, args.email), args.label)
                print(f"Token (chỉ hiện một lần): {token}")
            else:
                n = revoke_tokens(conn, _user_id(conn, args.email))
                print(f"Đã thu hồi {n} token.")
    finally:
        engine.dispose()
    return 0


def _mail_command(args: argparse.Namespace) -> int:
    worker = create_engine(database_url("worker"), hide_parameters=True)
    try:
        with worker.connect() as conn:
            if args.email:
                user_ids = [_user_id(conn, args.email)]
            else:
                user_ids = list(conn.scalars(text("select id from users order by id")))
            if args.cmd == "mail-stats":
                print(stats_report(conn, user_ids))
                return 0
    finally:
        worker.dispose()

    (user_id,) = user_ids
    if args.cmd == "process-mail":
        print(format_process_result(user_id, _run(_process(user_id, args.rethread))))
        return 0
    if args.cmd == "embed-mail":
        try:
            done, pending = _run(_embed(user_id, args.limit))
        except MlUnavailable:
            print(
                "Lỗi: không gọi được ml-service (chạy: python -m app.run trong ml-service)",
                file=sys.stderr,
            )
            return 1
        except EmbeddingModelMismatch as e:
            print(f"Lỗi: {e}. Đổi model phải embed lại toàn bộ mail.", file=sys.stderr)
            return 1
        print(f"Người dùng id={user_id}: đã embed {done} mail, còn chờ {pending}.")
        return 0

    api = create_engine(database_url("api"), hide_parameters=True)
    try:
        # Phiên app_api + app.user_id: RLS chỉ cho thấy mail của chính người này.
        with user_connection(api, user_id) as conn:
            html, picked = review_html(conn, user_id, args.sample, args.seed)
    finally:
        api.dispose()
    # Chỉ ghi vào thư mục mặc định (Claude Code bị chặn đọc, nằm ngoài repo và thư mục đồng bộ
    # cloud như Desktop/Documents); không cho chọn đường dẫn khác.
    out = default_review_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    pruned = prune_old_reviews(out.parent)
    out.write_text(html, encoding="utf-8")
    print(f"Đã ghi {picked} thread vào {out}")
    if pruned:
        print(f"Đã xóa {pruned} file review cũ hơn {REVIEW_KEEP_DAYS} ngày.")
    print("File có nội dung mail: chỉ mở trên máy này và xóa sau khi kiểm tra xong.")
    return 0


async def _process(user_id: int, rethread: bool) -> ProcessResult:
    engine = create_worker_engine()
    try:
        return await process_user(engine, user_id, force_rethread=rethread)
    finally:
        await engine.dispose()


async def _embed(user_id: int, limit: int) -> tuple[int, int]:
    """Embed theo lô (mới nhất trước) tới khi hết hoặc đủ limit mail. Trả (đã embed, còn chờ)."""
    engine = create_worker_engine()
    ml = HttpMlClient()
    done = 0
    try:
        while limit <= 0 or done < limit:
            n = EMBED_BATCH if limit <= 0 else min(EMBED_BATCH, limit - done)
            stats = await embed_batch(engine, ml, user_id, n)
            done += stats.messages
            if stats.messages:
                print(f"  … {done} mail", file=sys.stderr)
            if not stats.more:
                break
        return done, await count_pending(engine, user_id)
    finally:
        await engine.dispose()


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    # psycopg async không chạy trên ProactorEventLoop (mặc định của Windows).
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


def format_process_result(user_id: int, result: ProcessResult) -> str:
    c, t = result.clean, result.threads
    lines = [f"Người dùng id={user_id}: làm sạch {c.cleaned} mail (lỗi {c.failed})."]
    if t is None:
        lines.append("Gom thread: không có thay đổi cần gom lại.")
    else:
        lines.append(
            f"Gom thread: {t.messages} mail → {t.threads} thread; {t.changed} mail đổi thread,"
            f" tạo {t.created}, xóa {t.deleted} thread."
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
