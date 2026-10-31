"""Kiểm tra kết quả làm sạch / gom thread (spec 2026-10-17-worker-design.md mục 5, 6).

- stats_report: CHỈ số liệu tổng hợp, không chuỗi nào lấy từ mail. Claude Code chạy được trên dữ
  liệu thật.
- review_html: HTML gồm nội dung mail để NGƯỜI DÙNG tự kiểm tra. Gọi trong phiên app_api đã đặt
  app.user_id (RLS chỉ cho thấy mail của người đó). Claude Code không đọc file này.
"""

import base64
import hashlib
import os
import random
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from app.config import embed_model
from app.embedding import CHUNKER_VERSION
from app.mail.cleaning import CLEANER_VERSION
from app.mail.threading import THREADER_VERSION

METHODS = ("root", "references", "thread_index", "subject", "merge")
REVIEW_BODY_LIMIT = 20_000  # ký tự mỗi phần hiển thị, giữ file nhẹ
REVIEW_KEEP_DAYS = 7

# Embedding (spec 2026-10-19-mail-embedding-design): chỉ số đếm.
EMBED_STATS = text(
    """
    SELECT
        count(*) FILTER (WHERE ok.message_id IS NOT NULL) AS embedded,
        count(*) FILTER (WHERE ok.message_id IS NULL AND m.body_clean IS NOT NULL) AS pending,
        (SELECT count(*) FROM message_chunks c WHERE c.user_id = :u) AS chunks
    FROM messages m
    LEFT JOIN message_chunks ok
        ON ok.user_id = m.user_id AND ok.message_id = m.id AND ok.chunk_no = 0
       AND ok.model = :model AND ok.chunker_version = :cv
       AND ok.cleaner_version = m.cleaner_version
    WHERE m.user_id = :u
    """
)
MESSAGE_STATS = text(
    """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE cleaner_version = :cv) AS cleaned,
           count(*) FILTER (WHERE clean_cuts @> '[{"kind": "quote"}]') AS with_quote,
           count(*) FILTER (WHERE clean_cuts @> '[{"kind": "signature"}]') AS with_signature,
           coalesce(sum(char_length(body_text)) FILTER (WHERE body_clean IS NOT NULL), 0)
               AS text_chars,
           coalesce(sum(char_length(body_clean)), 0) AS clean_chars,
           count(*) FILTER (WHERE body_clean = '' AND btrim(body_text) <> '') AS emptied,
           count(*) FILTER (WHERE thread_id IS NULL) AS unthreaded
    FROM messages WHERE user_id = :u
    """
)
METHOD_STATS = text(
    "SELECT thread_method, count(*) AS n FROM messages "
    "WHERE user_id = :u AND thread_method IS NOT NULL GROUP BY thread_method"
)
THREAD_STATS = text(
    """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE message_count = 1) AS single,
           count(*) FILTER (WHERE message_count BETWEEN 2 AND 5) AS small,
           count(*) FILTER (WHERE message_count BETWEEN 6 AND 20) AS medium,
           count(*) FILTER (WHERE message_count > 20) AS large,
           coalesce(max(message_count), 0) AS biggest,
           count(*) FILTER (WHERE threader_version <> :tv) AS old_version
    FROM threads WHERE user_id = :u
    """
)


# Dự án (spec 25): chỉ số đếm, không lấy tên dự án.
PROJECT_STATS = text(
    """
    SELECT (SELECT count(*) FROM projects WHERE user_id = :u) AS projects,
           (SELECT count(DISTINCT message_id) FROM message_projects WHERE user_id = :u) AS assigned
    """
)


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "-"


def stats_report(conn: Connection, user_ids: list[int]) -> str:
    """Số liệu tổng hợp theo người dùng. Không in subject, địa chỉ, folder hay nội dung."""
    lines: list[str] = []
    for u in user_ids:
        m = conn.execute(MESSAGE_STATS, {"u": u, "cv": CLEANER_VERSION}).one()
        t = conn.execute(THREAD_STATS, {"u": u, "tv": THREADER_VERSION}).one()
        methods = {r.thread_method: r.n for r in conn.execute(METHOD_STATS, {"u": u})}
        reduced = 1 - m.clean_chars / m.text_chars if m.text_chars else 0
        e = conn.execute(EMBED_STATS, {"u": u, "model": embed_model(), "cv": CHUNKER_VERSION}).one()
        p = conn.execute(PROJECT_STATS, {"u": u}).one()
        lines += [
            f"Người dùng id={u}",
            f"  Embedding ({embed_model()}, cắt đoạn v{CHUNKER_VERSION}): đã embed {e.embedded}"
            f" mail · chờ embed: {e.pending} · {e.chunks} đoạn",
            f"  Dự án: {p.projects} dự án · {p.assigned} mail đã gán dự án",
            f"  Mail: {m.total} · đã làm sạch (v{CLEANER_VERSION}): {m.cleaned}"
            f" · chờ làm sạch: {m.total - m.cleaned} · chưa gom thread: {m.unthreaded}",
            f"  Làm sạch: cắt quote {m.with_quote} ({_pct(m.with_quote, m.total)})"
            f" · cắt chữ ký {m.with_signature} ({_pct(m.with_signature, m.total)})"
            f" · {m.text_chars:,} → {m.clean_chars:,} ký tự (giảm {reduced * 100:.1f}%)"
            f" · thành rỗng: {m.emptied}",
            f"  Thread: {t.total} · 1 mail: {t.single} · 2–5: {t.small} · 6–20: {t.medium}"
            f" · >20: {t.large} · lớn nhất: {t.biggest} mail"
            + (f" · version cũ: {t.old_version}" if t.old_version else ""),
            "  Mail theo cách gom: "
            + " · ".join(
                f"{k} {methods.get(k, 0)} ({_pct(methods.get(k, 0), m.total)})" for k in METHODS
            ),
        ]
    return "\n".join(lines) if lines else "Chưa có người dùng."


def default_review_path(now: datetime | None = None) -> Path:
    """%LOCALAPPDATA%\\PMMailAssistant\\review\\threads-<thời gian>.html.

    .claude/settings.json chặn Claude Code đọc thư mục này.
    """
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return root / "PMMailAssistant" / "review" / f"threads-{stamp}.html"


def prune_old_reviews(directory: Path, keep_days: int = REVIEW_KEEP_DAYS) -> int:
    """Xóa file review cũ (có nội dung mail) để không nằm lại trên máy. Trả số file đã xóa."""
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in directory.glob("threads-*.html"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    return removed


# Lọc user_id tường minh, không chỉ dựa vào RLS: khi có chia sẻ thread theo dự án, RLS sẽ cho thấy
# cả thread của người khác, nhưng file review chỉ được chứa mail của người được chỉ định.
REVIEW_THREADS = text("SELECT id FROM threads WHERE user_id = :u ORDER BY id")
REVIEW_THREAD = text(
    """
    SELECT t.id, t.message_count, t.first_message_at, t.last_message_at, r.subject
    FROM threads t
    LEFT JOIN messages r
        ON r.user_id = t.user_id AND r.thread_id = t.id AND r.thread_method = 'root'
    WHERE t.user_id = :u AND t.id = :t
    """
)
REVIEW_MESSAGES = text(
    """
    SELECT id, sent_at, from_name, from_addr, subject, thread_method, thread_parent_id,
           body_text, body_clean, clean_cuts
    FROM messages WHERE user_id = :u AND thread_id = :t ORDER BY sent_at NULLS LAST, id
    """
)


def review_html(
    conn: Connection, user_id: int, sample: int, seed: int | None = None
) -> tuple[str, int]:
    """HTML của tối đa `sample` thread ngẫu nhiên. conn: phiên app_api đã đặt app.user_id."""
    thread_ids = list(conn.scalars(REVIEW_THREADS, {"u": user_id}))
    picked = random.Random(seed).sample(thread_ids, min(sample, len(thread_ids)))
    sections = [
        _thread_section(conn, user_id, i + 1, len(picked), tid)
        for i, tid in enumerate(sorted(picked))
    ]
    return _page(sections, len(thread_ids)), len(picked)


def _thread_section(conn: Connection, user_id: int, index: int, count: int, thread_id: int) -> str:
    params = {"u": user_id, "t": thread_id}
    t = conn.execute(REVIEW_THREAD, params).one()
    messages = conn.execute(REVIEW_MESSAGES, params).all()
    items = "\n".join(_message_item(m) for m in messages)
    return (
        f'<section class="thread"><header><label><input type="checkbox" class="wrong"> Gom sai'
        f"</label><h2>{index}/{count} · {_e(t.subject or '(không có subject)')}</h2>"
        f'<p class="meta">{t.message_count} mail · {_e(_date(t.first_message_at))} → '
        f"{_e(_date(t.last_message_at))} · thread #{t.id}</p></header>"
        f"<ol>{items}</ol></section>"
    )


def _message_item(m: Any) -> str:
    sender = f"{m.from_name} <{m.from_addr}>" if m.from_name else (m.from_addr or "")
    cuts = m.clean_cuts or []
    cut_html = "".join(
        f'<p class="cut-kind">{_e(c["kind"])}</p>'
        f"<pre>{_e(_limit(m.body_text[c['start'] : c['end']]))}</pre>"
        for c in cuts
    )
    clean = m.body_clean if m.body_clean is not None else "(chưa làm sạch)"
    return (
        f'<li><p class="meta">{_e(_date(m.sent_at))} · {_e(sender)} · '
        f'<span class="method">{_e(m.thread_method or "-")}</span> · mail #{m.id}'
        f"{f' trả lời #{m.thread_parent_id}' if m.thread_parent_id else ''}</p>"
        f'<p class="subject">{_e(m.subject or "")}</p>'
        f"<pre>{_e(_limit(clean))}</pre>"
        + (
            f"<details><summary>Phần đã cắt ({len(cuts)})</summary>{cut_html}</details>"
            if cuts
            else ""
        )
        + "</li>"
    )


# Script duy nhất của trang; CSP chỉ cho chạy đúng script này (theo hash), nên nếu sau này có chỗ
# quên escape thì script chèn vào cũng không chạy.
_SCRIPT = """
document.addEventListener("change", () => {
  document.getElementById("wrong").textContent =
    document.querySelectorAll(".wrong:checked").length;
});
"""
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()


def _page(sections: list[str], total: int) -> str:
    # translate="no" / notranslate: trình duyệt không gợi ý dịch (gửi nội dung mail cho dịch vụ
    # dịch bên ngoài).
    return f"""<!doctype html>
<html lang="vi" translate="no"><head><meta charset="utf-8">
<meta name="google" content="notranslate">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{_SCRIPT_HASH}'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kiểm tra gom thread</title>
<style>
body {{ font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 960px; padding: 16px;
       color: #1d1d1f; background: #fff; }}
#bar {{ position: sticky; top: 0; background: #fff; padding: 8px 0;
        border-bottom: 1px solid #ddd; }}
.thread {{ border: 1px solid #ddd; border-radius: 8px; margin: 16px 0; padding: 8px 16px; }}
.thread:has(.wrong:checked) {{ border-color: #c62828; background: #fff5f5; }}
h2 {{ font-size: 16px; margin: 4px 0; }} .meta {{ color: #666; margin: 2px 0; font-size: 12px; }}
.subject {{ font-weight: 600; margin: 2px 0; }} .method {{ font-family: monospace; }}
ol {{ padding-left: 20px; }} li {{ margin: 12px 0; }}
pre {{ white-space: pre-wrap; word-break: break-word; background: #f6f6f6; padding: 8px;
      margin: 4px 0; }}
details pre {{ color: #888; }} .cut-kind {{ font-size: 12px; color: #888; margin: 4px 0 0; }}
</style></head><body>
<div id="bar"><strong>Kiểm tra gom thread</strong> · {len(sections)} thread ngẫu nhiên / {total}
 · Gom sai: <span id="wrong">0</span>/{len(sections)}
 · File này có nội dung mail: xóa sau khi kiểm tra xong.</div>
{"".join(sections)}
<script>{_SCRIPT}</script>
</body></html>
"""


def _e(value: str) -> str:
    return escape(value, quote=True)


def _limit(value: str) -> str:
    if len(value) <= REVIEW_BODY_LIMIT:
        return value
    return value[:REVIEW_BODY_LIMIT] + f"\n… (còn {len(value) - REVIEW_BODY_LIMIT:,} ký tự)"


def _date(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else "(không có ngày)"
