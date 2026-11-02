"""Đánh giá tìm kiếm (spec 2026-10-19-hybrid-search-design mục 7) trên profile Thunderbird GIẢ.

Chạy từ thư mục gốc (cần Docker DB và ml-service đang chạy; KHÔNG chạy cùng lúc với pytest vì cùng
dùng database test):

    uv run --directory backend python ../eval/retrieval_eval.py [--skip-load] [--out FILE]

1. Nạp data/thunderbird-profile (dữ liệu tự sinh, *.example.*) vào database TEST mailassist_test
   cho người dùng eval@vn-outsource.example.com (xóa dữ liệu cũ của người này trước).
2. Làm sạch, gom thread, embed bằng ml-service thật (bge-m3).
3. Với mỗi câu trong eval/questions-sample.jsonl: xếp hạng mail theo 4 cách (từ khóa AND như ô
   tìm kiếm, từ khóa OR, vector, hybrid RRF), đổi sang thread theo data/expected_threads.json, đo
   hit@1, hit@5, hit@10, MRR. In bảng Markdown (và ghi ra --out nếu có).
4. Spec 27: thêm hybrid_or_rerank (rerank 40 mail đầu của hybrid_or, đo thời gian) khi /health của
   ml-service có rerank_model, không thì bỏ dòng này và ghi chú; đo recall@120 của vector_chunks
   (HNSW) so với quét chính xác, không lọc và lọc theo folder lớn nhất.

Không bao giờ chạy trên database dev mailassist (có mail thật): URL lấy theo database test.
"""

import argparse
import asyncio
import email
import json
import re
import sys
import time
from email.header import decode_header, make_header
from email.message import Message
from email.policy import compat32
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

from psycopg.types.json import Jsonb
from sqlalchemy import create_engine, text

# Script nằm ngoài backend/: thêm backend vào sys.path để import app, tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ask import _rerank_texts
from app.ai.context import ChunkKey
from app.config import ml_service_url
from app.db.session import create_api_engine, user_session
from app.embedding import CHUNK_CHARS, count_pending, embed_user
from app.mail.hashing import message_id_hash, normalize_message_id
from app.ml_client import HttpMlClient
from app.processing import create_worker_engine, process_user
from app.search.keyword import MessageFilters, search_messages
from app.search.query import parse_query
from app.search.semantic import ChunkHit, best_per_message, keyword_messages, rrf, vector_chunks
from tests.dbutil import url_for_test_db

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "thunderbird-profile"
EXPECTED = ROOT / "data" / "expected_threads.json"
QUESTIONS = ROOT / "eval" / "questions-sample.jsonl"
EMAIL = "eval@vn-outsource.example.com"
KS = (1, 5, 10)


def _decode(value: str | None) -> str | None:
    return None if value is None else str(make_header(decode_header(value)))


def _body(msg: Message) -> str:
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html") and not part.get_filename():
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def _addresses(msg: Message, header: str) -> list[dict[str, str | None]]:
    return [
        {"name": _decode(n) or None, "addr": a.lower()}
        for n, a in getaddresses(msg.get_all(header, []))
        if a
    ]


def read_mbox(path: Path) -> list[Message]:
    """Tách mbox ở chế độ chỉ đọc (mailbox.mbox mở file "rb+"): dòng "From " đầu mail."""
    data = path.read_bytes()
    parts = re.split(rb"(?:^|\n)From [^\n]*\n", data)
    return [email.message_from_bytes(p, policy=compat32) for p in parts if p.strip()]


def load_profile(owner_url: str) -> int:
    owner = create_engine(owner_url)
    rows: dict[str, dict[str, object]] = {}
    for path in sorted(PROFILE.rglob("*")):
        if not path.is_file() or path.suffix or path.stat().st_size == 0:
            continue
        for msg in read_mbox(path):
            mid = normalize_message_id(msg["Message-ID"])
            if not mid or mid in rows:
                continue
            name, addr = parseaddr(msg["From"])
            refs = [normalize_message_id(r) for r in (msg["References"] or "").split()]
            rows[mid] = {
                "h": message_id_hash(mid, None, None, None, ""),
                "mid": mid,
                "irt": normalize_message_id(msg["In-Reply-To"]),
                "refs": [r for r in refs if r],
                "ti": msg["Thread-Index"],
                "subject": _decode(msg["Subject"]),
                "frm": addr.lower(),
                "fname": _decode(name) or None,
                "to": Jsonb(_addresses(msg, "To")),
                "cc": Jsonb(_addresses(msg, "Cc")),
                "at": parsedate_to_datetime(msg["Date"]),
                "body": _body(msg),
            }
    with owner.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": EMAIL})
        user = conn.scalar(
            text(
                "INSERT INTO users (email, display_name, role) VALUES (:e, 'Eval', 'pm') RETURNING id"
            ),
            {"e": EMAIL},
        )
        conn.execute(
            text(
                "INSERT INTO messages (user_id, message_id_hash, message_id, in_reply_to, "
                "references_ids, thread_index, subject, from_addr, from_name, to_addrs, cc_addrs, "
                "sent_at, body_text, parser_version) VALUES (:u, :h, :mid, :irt, :refs, :ti, "
                ":subject, :frm, :fname, :to, :cc, :at, :body, 'eval')"
            ),
            [{"u": user, **r} for r in rows.values()],
        )
    owner.dispose()
    print(f"Đã nạp {len(rows)} mail giả cho người dùng id={user}", file=sys.stderr)
    return int(user)


async def prepare(user: int, worker_url: str) -> None:
    worker = create_worker_engine(worker_url)
    try:
        await process_user(worker, user)
        ml = HttpMlClient()
        started = time.monotonic()
        while True:
            stats = await embed_user(worker, ml, user, budget_seconds=60)
            left = await count_pending(worker, user)
            print(
                f"  embed: +{stats.messages} mail, còn {left} ({time.monotonic() - started:.0f}s)",
                file=sys.stderr,
            )
            if not stats.more:
                break
    finally:
        await worker.dispose()


def weighted_rrf(rankings: list[tuple[list[int], float]], k: int = 60) -> list[int]:
    """RRF có trọng số theo nhánh (chỉ để so sánh trong eval)."""
    scores: dict[int, float] = {}
    for ranking, weight in rankings:
        for rank, m in enumerate(ranking, start=1):
            scores[m] = scores.get(m, 0.0) + weight / (k + rank)
    return sorted(scores, key=lambda m: (-scores[m], -m))


def thread_keys(ranked_mids: list[int], key_of: dict[int, str]) -> list[str]:
    out: list[str] = []
    for m in ranked_mids:
        k = key_of.get(m)
        if k is not None and k not in out:
            out.append(k)
    return out


RERANK_TOP = 40  # RERANK_CANDIDATES của /ask


def rerank_enabled_in_ml() -> str | None:
    """Tên model rerank của ml-service đang chạy (/health), None nếu tắt hoặc không gọi được."""
    try:
        resp = httpx.get(f"{ml_service_url()}/health", timeout=10, trust_env=False)
        return resp.json().get("rerank_model")
    except (httpx.HTTPError, ValueError):
        return None


def overlap(approx: list[ChunkHit], exact: list[ChunkHit]) -> float:
    """Tỷ lệ đoạn của kết quả quét chính xác có trong kết quả HNSW."""
    want = {(h.message_id, h.chunk_no) for h in exact}
    got = {(h.message_id, h.chunk_no) for h in approx}
    return len(want & got) / len(want) if want else 1.0


async def rerank_ranking(
    s: AsyncSession, ml: HttpMlClient, question: str, base: list[int], hits: list[ChunkHit]
) -> tuple[list[int], float]:
    """Xếp lại RERANK_TOP mail đầu của `base`, giữ phần đuôi. Mỗi mail dùng đoạn gần nhất."""
    head, tail = base[:RERANK_TOP], base[RERANK_TOP:]
    if len(head) < 2:
        return base, 0.0
    best = {h.message_id: h for h in reversed(hits)}  # đoạn đầu tiên (gần nhất) thắng
    keys: list[ChunkKey] = []
    for m in head:
        h = best.get(m)
        keys.append((m, h.start_pos, h.end_pos) if h else (m, 0, CHUNK_CHARS))
    texts = await _rerank_texts(s, keys)
    started = time.monotonic()
    scores = await ml.rerank(question, texts)
    took = time.monotonic() - started
    order = sorted(range(len(head)), key=lambda i: -scores[i])
    return [head[i] for i in order] + tail, took


async def evaluate(user: int, api_url: str) -> tuple[str, dict[str, dict[str, float]]]:
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines()]
    api = create_api_engine(api_url)
    ml = HttpMlClient()
    rerank_model = rerank_enabled_in_ml()
    notes: list[str] = []
    methods = [
        "keyword_and",
        "keyword_or",
        "vector",
        "hybrid_or",
        "hybrid_or_w03",
        "hybrid_and",
        "hybrid_and_or_w03",
    ]
    if rerank_model:
        methods.append("hybrid_or_rerank")
    else:
        notes.append("Bỏ dòng hybrid_or_rerank: /health của ml-service có rerank_model = null.")
    rerank_times: list[float] = []
    recalls: dict[str, list[float]] = {"không lọc": [], "lọc theo folder": []}
    scores: dict[str, dict[str, float]] = {
        m: {"mrr": 0.0, **{f"hit@{k}": 0.0 for k in KS}} for m in methods
    }
    lines = ["| Câu | " + " | ".join(methods) + " |", "|---|" + "---|" * len(methods)]
    try:
        group_of = {normalize_message_id(m): key for key, mids in expected.items() for m in mids}
        async with user_session(api, user) as s:
            key_of = {
                r.id: group_of[r.message_id]
                for r in await s.execute(text("SELECT id, message_id FROM messages"))
                if r.message_id in group_of
            }
        async with user_session(api, user) as s:
            folder_id = await s.scalar(
                text(
                    "SELECT source_id FROM message_ownership GROUP BY source_id "
                    "ORDER BY count(*) DESC, source_id LIMIT 1"
                )
            )
        folder_filter = MessageFilters(folder_id=folder_id) if folder_id is not None else None
        for q in questions:
            (qv,) = await ml.embed([q["question"]])
            async with user_session(api, user) as s:
                f = MessageFilters()
                rankings: dict[str, list[int]] = {}
                parsed = parse_query(q["question"])
                res = await search_messages(s, user, parsed, f, sort="relevance", limit=50)
                rankings["keyword_and"] = [h.message_id for h in res.items]
                any_q = parse_query(q["question"], any_term=True)
                rankings["keyword_or"] = await keyword_messages(s, any_q, f) if any_q else []
                hits = await vector_chunks(s, qv, f)
                rankings["vector"] = [h.message_id for h in best_per_message(hits)]
                vec, kand, kor = rankings["vector"], rankings["keyword_and"], rankings["keyword_or"]
                rankings["hybrid_or"] = [m for m, _ in rrf([vec, kor])]
                if rerank_model:
                    rankings["hybrid_or_rerank"], took = await rerank_ranking(
                        s, ml, q["question"], rankings["hybrid_or"], hits
                    )
                    rerank_times.append(took)
                # Recall HNSW so với quét chính xác (limit 120), có và không lọc folder.
                for label, flt in (
                    ("không lọc", MessageFilters()),
                    ("lọc theo folder", folder_filter),
                ):
                    if flt is None:
                        continue
                    approx = await vector_chunks(s, qv, flt, limit=120, exact=False)
                    exact = await vector_chunks(s, qv, flt, limit=120, exact=True)
                    recalls[label].append(overlap(approx, exact))
                rankings["hybrid_or_w03"] = weighted_rrf([(vec, 1.0), (kor, 0.3)])
                rankings["hybrid_and"] = [m for m, _ in rrf([vec, kand])]
                rankings["hybrid_and_or_w03"] = weighted_rrf([(vec, 1.0), (kand, 1.0), (kor, 0.3)])
            cells = []
            for m in methods:
                keys = thread_keys(rankings[m], key_of)
                rank = next((i for i, k in enumerate(keys, 1) if k in q["threads"]), None)
                if rank is not None:
                    scores[m]["mrr"] += 1 / rank
                for k in KS:
                    scores[m][f"hit@{k}"] += 1 if rank is not None and rank <= k else 0
                cells.append(str(rank) if rank else "–")
            lines.append(f"| {q['id']} ({q['lang']}) | " + " | ".join(cells) + " |")
    finally:
        await api.dispose()
    n = len(questions)
    for m in methods:
        for k in scores[m]:
            scores[m][k] /= n
    lines += ["", "| Cách | hit@1 | hit@5 | hit@10 | MRR |", "|---|---|---|---|---|"]
    for m in methods:
        sc = scores[m]
        lines.append(
            f"| {m} | {sc['hit@1']:.2f} | {sc['hit@5']:.2f} | {sc['hit@10']:.2f} | {sc['mrr']:.2f} |"
        )
    if rerank_times:
        avg = sum(rerank_times) / len(rerank_times)
        lines += [
            "",
            f"Thời gian rerank {RERANK_TOP} đoạn: trung bình {avg:.2f} giây ({rerank_model}).",
        ]
    lines += [
        "",
        "| Recall@120 của vector_chunks (HNSW) so với quét chính xác | Trung bình |",
        "|---|---|",
    ]
    for label, values in recalls.items():
        if values:
            lines.append(f"| {label} | {sum(values) / len(values):.3f} |")
    lines += [""] + notes if notes else []
    return "\n".join(lines), scores


async def main_async(args: argparse.Namespace) -> None:
    owner_url, worker_url, api_url = (url_for_test_db(r) for r in ("owner", "worker", "api"))
    assert "mailassist_test" in owner_url, "chỉ chạy trên database test"
    if args.skip_load:
        owner = create_engine(owner_url)
        with owner.connect() as conn:
            user = int(conn.scalar(text("SELECT id FROM users WHERE email = :e"), {"e": EMAIL}))
        owner.dispose()
    else:
        user = load_profile(owner_url)
    await prepare(user, worker_url)
    report, _ = await evaluate(user, api_url)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-load", action="store_true", help="dùng dữ liệu đã nạp lần trước")
    parser.add_argument("--out", help="ghi bảng kết quả ra file")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.run(main_async(args), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
