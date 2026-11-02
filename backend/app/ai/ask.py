"""Hỏi đáp trên mail (RAG): phân tích câu hỏi → tìm cục bộ → trả lời.

Spec 2026-10-19-ai-ask-design.md. Tìm kiếm chạy trong user_session (RLS) trước khi có gì được gửi
đi. Text gửi AI là text gốc, không che (spec 28: chỉ dùng model nội bộ). Không giữ transaction DB
trong lúc chờ AI. Không lưu, không log câu hỏi hay câu trả lời.
"""

import logging
import math
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
from app.ai.context import (
    PREVIOUS_MAX_CHARS,
    ChunkKey,
    PreviousMail,
    SourcePlan,
    group_sources,
    plan_context,
    render_body,
)
from app.ai.prompts import (
    ANALYSIS_SYSTEM,
    ANSWER_SYSTEM,
    AnswerOut,
    AnswerSource,
    QuestionPlan,
    analysis_message,
    answer_message,
)
from app.ai.settings import user_models
from app.config import rerank_enabled
from app.db.session import user_session
from app.embedding import CHUNK_CHARS, MAX_CHUNKS, MAX_SUBJECT_CHARS, embed_text, split_chunks
from app.ml_client import RERANK_MAX_TEXT_CHARS, MlClient, MlUnavailable, check_scores
from app.search.highlight import find_spans
from app.search.keyword import MessageFilters
from app.search.query import QueryError, parse_query
from app.search.semantic import keyword_messages, scope_is_small, vector_chunks
from app.web_schemas import AskCall, AskPlan, AskResult, AskSent, AskSource

log = logging.getLogger("app.ai")

CONTEXT_CHUNKS = 20
MAX_PER_MESSAGE = 3
RERANK_CANDIDATES = 40  # đoạn đứng đầu sau RRF đưa vào reranker (spec 27 mục 7)
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


class ProjectNotFound(Exception):
    """Dự án không tồn tại hoặc không thuộc người hỏi (RLS ẩn)."""


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
    project_id: int | None = None


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


async def _candidates(
    session: AsyncSession,
    vectors: Sequence[Sequence[float]],
    keyword_query: Any,
    filters: MessageFilters,
) -> list[ChunkKey]:
    rankings: list[list[ChunkKey]] = []
    exact = await scope_is_small(session, filters)  # đếm một lần cho mọi vector (spec 27 mục 4)
    for v in vectors:
        hits = await vector_chunks(session, v, filters, limit=VECTOR_K, exact=exact)
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
    return [key for key in fuse(rankings) if isinstance(key, tuple)]  # type: ignore[misc]


def pick(keys: Sequence[ChunkKey]) -> list[ChunkKey]:
    """Áp MAX_PER_MESSAGE và CONTEXT_CHUNKS lên danh sách đã xếp hạng."""
    picked: list[ChunkKey] = []
    per_message: dict[int, int] = {}
    for key in keys:
        mid = key[0]
        if per_message.get(mid, 0) >= MAX_PER_MESSAGE:
            continue
        per_message[mid] = per_message.get(mid, 0) + 1
        picked.append(key)
        if len(picked) >= CONTEXT_CHUNKS:
            break
    return picked


RERANK_BODIES = text(
    "SELECT m.id, m.subject, left(m.body_clean, :cap) AS body FROM messages m "
    "WHERE m.id = ANY(CAST(:ids AS bigint[]))"
)


async def _rerank_texts(session: AsyncSession, keys: Sequence[ChunkKey]) -> list[str]:
    """Text của từng đoạn như `embed_input`: 300 ký tự đầu subject, xuống dòng, đoạn. Phiên RLS."""
    rows = {
        r.id: r
        for r in await session.execute(
            RERANK_BODIES,
            {"ids": sorted({k[0] for k in keys}), "cap": MAX_CHUNKS * CHUNK_CHARS},
        )
    }
    out: list[str] = []
    for mid, start, end in keys:
        r = rows.get(mid)
        subject, body = (r.subject or "", r.body or "") if r is not None else ("", "")
        out.append(embed_text(subject, body[start:end])[:RERANK_MAX_TEXT_CHARS])
    return out


async def _rerank(
    ml: MlClient, question: str, keys: list[ChunkKey], texts: list[str]
) -> tuple[list[ChunkKey], int]:
    """Xếp lại theo điểm giảm dần (sorted ổn định: bằng điểm giữ thứ tự RRF). Gọi ngoài transaction.
    Lỗi bất kỳ của ml-service → giữ thứ tự RRF, trả 0 (không log câu hỏi, text, điểm)."""
    try:
        scores = check_scores(await ml.rerank(question, texts), len(keys))
    except MlUnavailable:
        return keys, 0
    if not all(math.isfinite(x) for x in scores):
        return keys, 0
    order = sorted(range(len(keys)), key=lambda i: -scores[i])
    return [keys[i] for i in order], len(keys)


SOURCES = text(
    """
    SELECT p.ord, m.id, m.thread_id, m.subject, m.from_name, m.from_addr, m.to_addrs, m.cc_addrs,
           m.sent_at, m.sent_tz_offset_min, left(coalesce(m.body_clean, ''), :cap) AS body
    FROM unnest(CAST(:ids AS bigint[])) WITH ORDINALITY AS p(id, ord)
    JOIN messages m ON m.id = p.id
    ORDER BY p.ord
    """
)

# Mail trước của mỗi nguồn (spec 27 mục 6). Chạy trong cùng phiên RLS với SOURCES; thêm điều kiện
# user_id và thread_id để mail trước luôn của người hỏi và cùng thread, kể cả khi RLS bị lỡ.
PREVIOUS_MAILS = text(
    """
    SELECT s.id AS source_id, p.id, p.from_name, p.from_addr, p.to_addrs, p.cc_addrs,
           p.sent_at, p.sent_tz_offset_min, left(p.body_clean, :previous_max) AS body
    FROM messages s
    JOIN LATERAL (
      SELECT x.* FROM messages x
      WHERE x.user_id = s.user_id AND x.thread_id = s.thread_id AND x.id <> s.id
        AND coalesce(x.body_clean, '') <> ''
        AND (x.id = s.thread_parent_id OR x.sent_at < s.sent_at)
      ORDER BY (x.id IS NOT DISTINCT FROM s.thread_parent_id) DESC,
               x.sent_at DESC NULLS LAST, x.id DESC
      LIMIT 1
    ) p ON true
    WHERE s.id = ANY(CAST(:ids AS bigint[]))
    """
)


async def _fetch_context(
    session: AsyncSession, picked: Sequence[ChunkKey]
) -> tuple[list[Any], list[SourcePlan], dict[int, Any]]:
    """Hàng nguồn (mỗi mail một hàng, theo thứ tự `group_sources`), kế hoạch ngữ cảnh và hàng mail
    trước theo id. Cùng phiên RLS của người hỏi. Chặn ở CONTEXT_CHUNKS đoạn để lớp đảm bảo của
    `plan_context` có trần (≤ 20 × 800 ký tự)."""
    groups = group_sources(picked[:CONTEXT_CHUNKS])
    if not groups:
        return [], [], {}
    ids = [g.message_id for g in groups]
    cap = MAX_CHUNKS * CHUNK_CHARS
    by_id = {r.id: r for r in await session.execute(SOURCES, {"ids": ids, "cap": cap})}
    groups = [g for g in groups if g.message_id in by_id]
    rows = [by_id[g.message_id] for g in groups]
    previous_rows = {
        r.source_id: r
        for r in await session.execute(
            PREVIOUS_MAILS,
            {"ids": [g.message_id for g in groups], "previous_max": PREVIOUS_MAX_CHARS},
        )
    }
    plans = plan_context(
        groups,
        {r.id: len(r.body) for r in rows},  # 0 cho mail chỉ có subject
        {sid: PreviousMail(r.id, len(r.body)) for sid, r in previous_rows.items()},
    )
    return rows, plans, {r.id: r for r in previous_rows.values()}


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


def _header(r: Any, *, full: bool) -> list[str]:
    lines = [
        f"Date: {_local_time(r.sent_at, r.sent_tz_offset_min)}",
        f"From: {_person(r.from_name, r.from_addr)}",
    ]
    if full:
        lines.append(f"To: {_addresses(r.to_addrs)}")
        if cc := _addresses(r.cc_addrs):
            lines.append(f"Cc: {cc}")
        lines.append(f"Subject: {(r.subject or '')[:MAX_SUBJECT_CHARS]}")
    return lines


def _source_text(r: Any, plan: SourcePlan) -> str:
    """Một nguồn gửi AI: header (đã giới hạn độ dài, chi phí có trần) và thân mail đã mở rộng."""
    return "\n".join([*_header(r, full=True), "", render_body(r.body, plan.spans).strip()])


def _previous_text(r: Any, plan: SourcePlan) -> str:
    """Mail trước: Date, From và `previous_chars` ký tự đầu của thân mail."""
    return "\n".join([*_header(r, full=False), "", r.body[: plan.previous_chars].strip()])


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
        if filters.project_id is not None and not await s.scalar(
            text("SELECT 1 FROM projects WHERE id = :id"), {"id": filters.project_id}
        ):
            raise ProjectNotFound
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
        project_id=filters.project_id,
    )
    narrowed = MessageFilters(
        folder_id=base.folder_id,
        project_id=base.project_id,
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
    texts: list[str] | None = None
    async with user_session(engine, user_id) as s:
        candidates = await _candidates(s, vectors, keyword_query, narrowed)
        if not candidates and narrowed != base:
            relaxed = True
            candidates = await _candidates(s, vectors, keyword_query, base)
        head = candidates[:RERANK_CANDIDATES]
        if question and len(head) >= 2 and rerank_enabled():
            texts = await _rerank_texts(s, head)
    # Gọi reranker ngoài transaction (không giữ kết nối DB trong lúc chờ ml-service).
    reranked = 0
    if texts is not None:
        head, reranked = await _rerank(ml, question, head, texts)
        candidates = [*head, *candidates[RERANK_CANDIDATES:]]
    picked = pick(candidates)
    rows: list[Any] = []
    plans: list[SourcePlan] = []
    previous_rows: dict[int, Any] = {}
    if picked:
        async with user_session(engine, user_id) as s:
            rows, plans, previous_rows = await _fetch_context(s, picked)
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
            "ask user=%s sources=%d calls=%d reranked=%d ms=%d",
            user_id,
            len(sources),
            len(calls.calls),
            reranked,
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
    content = answer_message(
        question,
        [
            AnswerSource(
                _source_text(r, plan_),
                _previous_text(previous_rows[plan_.previous_id], plan_)
                if plan_.previous_id is not None
                else None,
            )
            for r, plan_ in zip(rows, plans, strict=True)
        ],
    )
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
