"""Hỏi đáp trên mail (RAG): che → phân tích câu hỏi → tìm cục bộ → che nguồn → trả lời → thay ngược.

Spec 2026-10-19-ai-ask-design.md. Tìm kiếm chạy trong user_session (RLS) trước khi có gì được gửi
đi; chỉ text đã che (Masker, spec 20) tới AI. Không giữ transaction DB trong lúc chờ AI.
Không lưu, không log câu hỏi hay câu trả lời.
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

from app.ai import people as people_mod
from app.ai.client import CallLog, LlmClient, call_task
from app.ai.masking import URL_BREAK_CHARS, Masker, cut_masked
from app.ai.names import companies_in
from app.ai.prompts import (
    ANALYSIS_SYSTEM,
    ANSWER_SYSTEM,
    AnswerOut,
    QuestionPlan,
    analysis_message,
    answer_message,
)
from app.ai.settings import user_models, user_tiers
from app.db.session import user_session
from app.embedding import CHUNK_CHARS, MAX_CHUNKS, MAX_SUBJECT_CHARS, split_chunks
from app.ml_client import MlClient
from app.schemas import MAX_INGEST_ADDRESS_CHARS, MAX_INGEST_SUBJECT_CHARS
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


WINDOW_CHARS = 200  # đọc dư mỗi bên đoạn khi che: đoạn cắt không theo ranh giới giá trị

SOURCES = text(
    f"""
    SELECT p.ord, m.id, m.thread_id, m.subject, m.from_name, m.from_addr, m.to_addrs, m.cc_addrs,
           m.sent_at, m.sent_tz_offset_min, p.s AS chunk_start, p.e AS chunk_end,
           substr(coalesce(m.body_clean, ''), greatest(p.s - {WINDOW_CHARS}, 0) + 1,
                  p.e + {WINDOW_CHARS} - greatest(p.s - {WINDOW_CHARS}, 0)) AS chunk_window,
           left(m.body_text, 20000) AS full_text
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
MAX_NAME_CHARS = 100
MAX_PERSON_CHARS = 2 * MAX_NAME_CHARS + 3  # "tên <địa chỉ>"
MAX_PEOPLE_CHARS = MAX_ADDRESSES * (MAX_PERSON_CHARS + 2) + 30  # thêm "(+N người khác)"


def _person(name: Any, addr: Any) -> str:
    """Giá trị đưa đi che: trần bằng giới hạn của ingest, cắt sau khi che (_source_text)."""
    name = str(name)[:MAX_INGEST_ADDRESS_CHARS] if name else ""
    addr = str(addr or "")[:MAX_INGEST_ADDRESS_CHARS]
    return f"{name} <{addr}>" if name else addr


def _addresses(items: Any) -> str:
    people = [a for a in items or [] if isinstance(a, dict)]
    out = [_person(a.get("name"), a.get("addr")) for a in people[:MAX_ADDRESSES]]
    if len(people) > MAX_ADDRESSES:
        out.append(f"(+{len(people) - MAX_ADDRESSES} người khác)")
    return ", ".join(out)


SOURCE_FIELDS = ("From", "To", "Cc", "Subject")
SOURCE_LIMITS = {
    "From": MAX_PERSON_CHARS,
    "To": MAX_PEOPLE_CHARS,
    "Cc": MAX_PEOPLE_CHARS,
    "Subject": MAX_SUBJECT_CHARS,
}


def _source_fields(r: Any) -> list[str]:
    """Giá trị cần che của một nguồn: người gửi, người nhận, cc, subject, đoạn mail. Che riêng
    từng giá trị (tên trường "From:", "Subject:" không đi qua NER: GiNZA hay nhận nhầm là tổ
    chức). Mọi phần có giới hạn độ dài: dưới giới hạn của /ner, chi phí gọi AI có trần. Header
    cắt sau khi che (_source_text): cắt trước thì mảnh địa chỉ, số điện thoại không khớp mẫu che.
    Phần đưa đi che tối đa bằng giới hạn của ingest: một lần hỏi có tới CONTEXT_CHUNKS nguồn."""
    return [
        _person(r.from_name, r.from_addr),
        _addresses(r.to_addrs),
        _addresses(r.cc_addrs),
        (r.subject or "")[:MAX_INGEST_SUBJECT_CHARS],
        r.chunk_window,
    ]


def _chunk_range(r: Any) -> tuple[int, int]:
    """Vị trí đoạn trong chunk_window, phần Masker xuất ra.

    Che trên cả cửa sổ: đoạn bắt đầu ngay sau nhãn "契約番号 " hay cắt đôi địa chỉ thì riêng đoạn
    không khớp mẫu nào. Chuỗi liền (không khoảng trắng, không dấu đóng hay 、。; kể cả ký tự
    không ASCII của URL chưa mã hóa) dài hơn phần đệm, như URL dài, có đầu nằm ngoài cửa sổ nên
    cửa sổ cũng không khớp: bỏ phần đuôi của nó khỏi đoạn, không gửi đi.
    """
    start = min(r.chunk_start, WINDOW_CHARS)
    end = start + r.chunk_end - r.chunk_start
    if r.chunk_start > WINDOW_CHARS:
        window = r.chunk_window
        run = next(
            (i for i, c in enumerate(window) if c.isspace() or c in URL_BREAK_CHARS), len(window)
        )
        start = max(start, min(run, end))
    return start, end


def _mask_ranges(r: Any) -> list[tuple[int, int] | None]:
    """Tham số keep của Masker.mask, song song với _source_fields."""
    return [*([None] * len(SOURCE_FIELDS)), _chunk_range(r)]


def _source_text(r: Any, masked: Sequence[str]) -> str:
    """Ghép một nguồn từ các giá trị đã che (theo thứ tự của _source_fields)."""
    lines = [f"Date: {_local_time(r.sent_at, r.sent_tz_offset_min)}"]
    for name, value in zip(SOURCE_FIELDS, masked, strict=False):
        capped = name == "Subject" and len(r.subject or "") > MAX_INGEST_SUBJECT_CHARS
        value, _ = cut_masked(value, SOURCE_LIMITS[name], capped=capped)
        if value or name != "Cc":
            lines.append(f"{name}: {value}")
    lines += ["", masked[len(SOURCE_FIELDS)].strip()]
    return "\n".join(lines)


def _participants(rows: Sequence[Any]) -> list[tuple[str | None, str | None]]:
    out: list[tuple[str | None, str | None]] = []
    for r in rows:
        out.append((r.from_name, r.from_addr))
        for group in (r.to_addrs or [], r.cc_addrs or []):
            for a in group[:MAX_ADDRESSES]:
                if isinstance(a, dict):
                    out.append((a.get("name"), a.get("addr")))
    return out


def learn_rows(masker: Masker, rows: Sequence[Any]) -> None:
    """Thêm vào từ điển che người gửi/nhận và tên công ty trong toàn văn (kể cả chữ ký đã cắt
    khỏi phần gửi đi) của các mail. Hàng cần cột from_name, from_addr, to_addrs, cc_addrs,
    full_text."""
    masker.add_entities(people_mod.entities_of(_participants(rows)))
    masker.add_entities(companies_in(r.full_text for r in rows if r.full_text))


async def build_masker(
    session: AsyncSession, ml: MlClient, user_id: int, rows: Sequence[Any]
) -> Masker:
    """Masker có từ điển dùng chung cho hỏi đáp và dịch mail.

    session phải là user_session của engine role app_api: correspondents dựa vào RLS, không lọc
    user_id trong câu truy vấn (role worker sẽ đọc lẫn người gửi/nhận của người khác).
    """
    masker = Masker(ml.ner)
    masker.add_entities(await people_mod.correspondents(session, user_id))
    learn_rows(masker, rows)
    return masker


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
    custom_provider: bool = False,
) -> AskResult:
    started = time.monotonic()
    question = question.replace("\x00", "").strip()
    today = today or datetime.now(UTC).date()
    calls = CallLog()
    sent: list[AskSent] = []

    async with user_session(engine, user_id) as s:
        tiers = await user_tiers(s)
        models = await user_models(s) if custom_provider else {}
        masker = await build_masker(s, ml, user_id, [])

    # 1. Phân tích câu hỏi (câu hỏi đã che).
    (masked_q,) = await masker.mask([question])
    content = analysis_message(today.isoformat(), masked_q)
    sent.append(AskSent(task="question_analysis", content=content))
    plan = await call_task(
        engine,
        llm,
        user_id,
        "question_analysis",
        tiers["question_analysis"],
        system=ANALYSIS_SYSTEM,
        user=content,
        output_type=QuestionPlan,
        log_to=calls,
        custom_model=models.get("question_analysis"),
    )
    queries = [masker.unmask(q).strip() for q in plan.search_queries if q.strip()][:MAX_QUERIES]
    keywords = [masker.unmask(k).strip() for k in plan.keywords if k.strip()][:MAX_KEYWORDS]
    sender = masker.unmask(plan.sender).strip() if plan.sender and plan.sender.strip() else None
    after, before = _parse_date(plan.sent_after), _parse_date(plan.sent_before)

    # 2. Tìm đoạn mail (cục bộ, RLS). Câu hỏi gốc chỉ đi tới ml-service trên máy này.
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
            mask_counts=masker.counts,
            calls=[AskCall(**vars(c)) for c in calls.calls],
        )

    if not rows:
        return result(NO_SOURCES_ANSWER, True, [])

    # 3. Che nguồn (cùng bảng nhãn), che lại câu hỏi với từ điển đã mở rộng, rồi hỏi AI.
    learn_rows(masker, rows)
    fields = [_source_fields(r) for r in rows]
    masked = await masker.mask(
        [question, *(v for f in fields for v in f)],
        keep=[None, *(k for r in rows for k in _mask_ranges(r))],
    )
    width = len(SOURCE_FIELDS) + 1
    sources_text = [
        _source_text(r, masked[1 + i * width : 1 + (i + 1) * width]) for i, r in enumerate(rows)
    ]
    content = answer_message(masked[0], sources_text)
    sent.append(AskSent(task="answer", content=content))
    out = await call_task(
        engine,
        llm,
        user_id,
        "answer",
        tiers["answer"],
        system=ANSWER_SYSTEM,
        user=content,
        output_type=AnswerOut,
        log_to=calls,
        custom_model=models.get("answer"),
    )
    n_sources = len(rows)
    answer = masker.unmask(out.answer)
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
