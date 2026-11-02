"""Hỏi đáp trên mail (RAG): phân tích câu hỏi → tìm cục bộ → trả lời.

Spec 2026-10-19-ai-ask-design.md. Tìm kiếm chạy trong user_session (RLS) trước khi có gì được gửi
đi. Text gửi AI là text gốc, không che (spec 28: chỉ dùng model nội bộ). Không giữ transaction DB
trong lúc chờ AI. Không lưu, không log câu hỏi hay câu trả lời.
"""

import logging
import re
import time
from collections.abc import Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.ai.client import CallLog, LlmClient, LlmError, call_task
from app.ai.prompts import (
    ANALYSIS_SYSTEM,
    ANSWER_SYSTEM,
    AnswerOut,
    QuestionPlan,
    analysis_message,
    answer_message,
)
from app.ai.settings import user_models
from app.db.session import user_session
from app.embedding import CHUNK_CHARS, MAX_CHUNKS, MAX_SUBJECT_CHARS, split_chunks
from app.ml_client import MlClient
from app.search.highlight import find_spans
from app.search.keyword import MessageFilters
from app.search.query import QueryError, parse_query
from app.search.semantic import keyword_messages, vector_chunks
from app.web_schemas import AskCall, AskPlan, AskResult, AskSent, AskSource

log = logging.getLogger("app.ai")

CONTEXT_CHUNKS = 20
MAX_PER_MESSAGE = 3
VECTOR_K = 120  # đoạn gần nhất cho mỗi câu tìm kiếm
KEYWORD_K = 40  # mail khớp từ khóa
RRF_K = 60
MAX_QUERIES = 3
MAX_KEYWORDS = 6
ASK_TASKS = ("question_analysis", "answer")  # tác vụ cần tên model (Cài đặt AI)
NO_SOURCES_ANSWER = (
    "Không tìm thấy mail liên quan tới câu hỏi trong phạm vi bạn được xem "
    "(hoặc mail chưa được xử lý cho tìm kiếm ngữ nghĩa)."
)

type ChunkKey = tuple[int, int, int]  # (message_id, start, end)


class AskBusy(Exception):
    """Người dùng đã có một câu hỏi đang chạy."""


class AskGuard:
    """Mỗi người dùng một câu hỏi tại một thời điểm (trong một process API)."""

    def __init__(self) -> None:
        self._active: set[int] = set()

    @contextmanager
    def hold(self, user_id: int) -> Iterator[None]:
        if user_id in self._active:
            raise AskBusy
        self._active.add(user_id)
        try:
            yield
        finally:
            self._active.discard(user_id)


@dataclass(frozen=True)
class AskFilters:
    folder_id: int | None = None
    sent_after: datetime | None = None
    sent_before: datetime | None = None


def fuse(rankings: Sequence[Sequence[Hashable]], k: int = RRF_K) -> list[Hashable]:
    """RRF trên khóa bất kỳ; bằng điểm giữ thứ tự xuất hiện đầu tiên."""
    scores: dict[Hashable, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking), start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda key: -scores[key])


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _keyword_query(keywords: Sequence[str]) -> Any:
    terms = [k.replace('"', " ").strip() for k in keywords]
    terms = [t for t in terms if t]
    if not terms:
        return None
    try:
        return parse_query(" ".join(f'"{t}"' for t in terms), any_term=True)
    except QueryError:
        return None


KEYWORD_BODIES = text(
    "SELECT m.id, left(m.body_clean, :cap) AS body FROM messages m "
    "WHERE m.id = ANY(CAST(:ids AS bigint[])) AND m.body_clean IS NOT NULL"
)


async def _retrieve(
    session: AsyncSession,
    vectors: Sequence[Sequence[float]],
    keyword_query: Any,
    filters: MessageFilters,
) -> list[ChunkKey]:
    rankings: list[list[ChunkKey]] = []
    for v in vectors:
        hits = await vector_chunks(session, v, filters, limit=VECTOR_K)
        rankings.append([(h.message_id, h.start, h.end) for h in hits])
    if keyword_query is not None:
        ids = await keyword_messages(session, keyword_query, filters, limit=KEYWORD_K)
        if ids:
            bodies = {
                r.id: r.body
                for r in await session.execute(
                    KEYWORD_BODIES, {"ids": ids, "cap": MAX_CHUNKS * CHUNK_CHARS}
                )
            }
            keys: list[ChunkKey] = []
            for mid in ids:
                body = bodies.get(mid)
                if body is None:
                    continue
                chunks = split_chunks(body)
                # Đoạn có từ khóa đầu tiên; không có (khớp ở subject) thì đoạn đầu.
                best = next(
                    (
                        c
                        for c in chunks
                        if find_spans(body[c.start : c.end], keyword_query.keywords)
                    ),
                    chunks[0],
                )
                keys.append((mid, best.start, best.end))
            rankings.append(keys)
    picked: list[ChunkKey] = []
    per_message: dict[int, int] = {}
    for key in fuse(rankings):
        assert isinstance(key, tuple)
        mid = key[0]
        if per_message.get(mid, 0) >= MAX_PER_MESSAGE:
            continue
        per_message[mid] = per_message.get(mid, 0) + 1
        picked.append(key)  # type: ignore[arg-type]
        if len(picked) >= CONTEXT_CHUNKS:
            break
    return picked


SOURCES = text(
    """
    SELECT p.ord, m.id, m.thread_id, m.subject, m.from_name, m.from_addr, m.to_addrs, m.cc_addrs,
           m.sent_at, m.sent_tz_offset_min,
           substr(coalesce(m.body_clean, ''), p.s + 1, p.e - p.s) AS chunk
    FROM unnest(CAST(:ids AS bigint[]), CAST(:starts AS integer[]), CAST(:ends AS integer[]))
         WITH ORDINALITY AS p(id, s, e, ord)
    JOIN messages m ON m.id = p.id
    ORDER BY p.ord
    """
)


def _local_time(sent_at: datetime | None, offset_min: int | None) -> str:
    if sent_at is None:
        return "?"
    tz = timezone(timedelta(minutes=offset_min)) if offset_min is not None else UTC
    return sent_at.astimezone(tz).strftime("%Y-%m-%d %H:%M %z")


MAX_ADDRESSES = 10  # To/Cc tối đa 1.000 địa chỉ: chỉ gửi 10 người đầu
MAX_NAME_CHARS = 100  # tên và địa chỉ, mỗi phần


def _person(name: Any, addr: Any) -> str:
    name = str(name)[:MAX_NAME_CHARS] if name else ""
    addr = str(addr or "")[:MAX_NAME_CHARS]
    return f"{name} <{addr}>" if name else addr


def _addresses(items: Any) -> str:
    people = [a for a in items or [] if isinstance(a, dict)]
    out = [_person(a.get("name"), a.get("addr")) for a in people[:MAX_ADDRESSES]]
    if len(people) > MAX_ADDRESSES:
        out.append(f"(+{len(people) - MAX_ADDRESSES} người khác)")
    return ", ".join(out)


def _source_text(r: Any) -> str:
    """Một nguồn gửi AI: header (đã giới hạn độ dài, chi phí có trần) và đoạn mail."""
    lines = [
        f"Date: {_local_time(r.sent_at, r.sent_tz_offset_min)}",
        f"From: {_person(r.from_name, r.from_addr)}",
        f"To: {_addresses(r.to_addrs)}",
    ]
    if cc := _addresses(r.cc_addrs):
        lines.append(f"Cc: {cc}")
    lines += [f"Subject: {(r.subject or '')[:MAX_SUBJECT_CHARS]}", "", r.chunk.strip()]
    return "\n".join(lines)


_CITE = re.compile(r"\[(\d{1,3})\]")


async def ask(
    engine: AsyncEngine,
    ml: MlClient,
    llm: LlmClient,
    user_id: int,
    question: str,
    filters: AskFilters,
    *,
    today: date | None = None,
) -> AskResult:
    started = time.monotonic()
    question = question.replace("\x00", "").strip()
    today = today or datetime.now(UTC).date()
    calls = CallLog()
    sent: list[AskSent] = []

    async with user_session(engine, user_id) as s:
        models = await user_models(s, user_id)
        if any(t not in models for t in ASK_TASKS):
            raise LlmError(
                "ai_model_missing", 503, "Nhập tên model cho tác vụ hỏi đáp (Cài đặt AI)"
            )

    # 1. Phân tích câu hỏi.
    content = analysis_message(today.isoformat(), question)
    sent.append(AskSent(task="question_analysis", content=content))
    plan = await call_task(
        engine,
        llm,
        user_id,
        "question_analysis",
        models["question_analysis"],
        system=ANALYSIS_SYSTEM,
        user=content,
        output_type=QuestionPlan,
        log_to=calls,
    )
    queries = [q.strip() for q in plan.search_queries if q.strip()][:MAX_QUERIES]
    keywords = [k.strip() for k in plan.keywords if k.strip()][:MAX_KEYWORDS]
    sender = plan.sender.strip() if plan.sender and plan.sender.strip() else None
    after, before = _parse_date(plan.sent_after), _parse_date(plan.sent_before)

    # 2. Tìm đoạn mail (cục bộ, RLS).
    base = MessageFilters(
        folder_id=filters.folder_id,
        sent_after=filters.sent_after,
        sent_before=filters.sent_before,
    )
    narrowed = MessageFilters(
        folder_id=base.folder_id,
        sender=sender[:200] if sender else None,
        sent_after=max(
            (d for d in (base.sent_after, _day_start(after) if after else None) if d), default=None
        ),
        sent_before=min(
            (d for d in (base.sent_before, _day_start(before) if before else None) if d),
            default=None,
        ),
    )
    vectors = await ml.embed([question, *queries])
    keyword_query = _keyword_query(keywords)
    relaxed = False
    async with user_session(engine, user_id) as s:
        picked = await _retrieve(s, vectors, keyword_query, narrowed)
        if not picked and narrowed != base:
            relaxed = True
            picked = await _retrieve(s, vectors, keyword_query, base)
        rows = (
            list(
                await s.execute(
                    SOURCES,
                    {
                        "ids": [k[0] for k in picked],
                        "starts": [k[1] for k in picked],
                        "ends": [k[2] for k in picked],
                    },
                )
            )
            if picked
            else []
        )
    ask_plan = AskPlan(
        search_queries=queries,
        keywords=keywords,
        sender=sender,
        sent_after=after,
        sent_before=before,
        filters_relaxed=relaxed,
    )

    def result(answer: str, insufficient: bool, sources: list[AskSource]) -> AskResult:
        log.info(
            "ask user=%s sources=%d calls=%d ms=%d",
            user_id,
            len(sources),
            len(calls.calls),
            (time.monotonic() - started) * 1000,
        )
        return AskResult(
            answer=answer,
            insufficient=insufficient,
            sources=sources,
            plan=ask_plan,
            sent=sent,
            calls=[AskCall(**vars(c)) for c in calls.calls],
        )

    if not rows:
        return result(NO_SOURCES_ANSWER, True, [])

    # 3. Trả lời từ các nguồn đã tìm.
    content = answer_message(question, [_source_text(r) for r in rows])
    sent.append(AskSent(task="answer", content=content))
    out = await call_task(
        engine,
        llm,
        user_id,
        "answer",
        models["answer"],
        system=ANSWER_SYSTEM,
        user=content,
        output_type=AnswerOut,
        log_to=calls,
    )
    n_sources = len(rows)
    answer = out.answer
    cited = {n for n in out.cited_sources if 1 <= n <= n_sources}
    cited |= {int(m.group(1)) for m in _CITE.finditer(answer) if 1 <= int(m.group(1)) <= n_sources}
    sources = [
        AskSource(
            n=i,
            message_id=r.id,
            thread_id=r.thread_id,
            subject=r.subject,
            from_name=r.from_name,
            from_addr=r.from_addr,
            sent_at=r.sent_at,
            cited=i in cited,
        )
        for i, r in enumerate(rows, start=1)
    ]
    return result(answer, out.insufficient, sources)
