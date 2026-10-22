"""Tìm kiếm ngữ nghĩa hybrid: nhánh vector (pgvector) + nhánh từ khóa (PGroonga), gộp bằng RRF.

Spec 2026-10-19-hybrid-search-design.md. Chạy trong user_session (app_api + RLS): lọc quyền và bộ
lọc nằm trong cùng truy vấn. Vector câu tìm kiếm truyền bằng tham số bind. Không log câu tìm kiếm.
Dùng chung cho hỏi đáp AI (spec 21) qua vector_chunks / keyword_messages / rrf.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import embed_model
from app.embedding import CHUNKER_VERSION, COUNT_PENDING
from app.ml_client import vector_literal
from app.search.highlight import highlight, snippet
from app.search.keyword import MATCH, MessageFilters, filter_conditions, folders_of
from app.search.query import ParsedQuery
from app.web_schemas import SearchHit, SearchResult

log = logging.getLogger("app.search")

RRF_K = 60
VECTOR_CHUNKS = 300  # số đoạn gần nhất lấy từ index trước khi gộp theo mail
BRANCH_LIMIT = 100  # số mail giữ ở mỗi nhánh
MAX_RESULTS = 200
EF_SEARCH = 200
PENDING_CAP = 10_001

# pgvector 0.8: iterative scan quét tiếp index khi bộ lọc (RLS, folder, ngày…) loại bớt đoạn.
# relaxed_order: thứ tự có thể lệch nhẹ; kết quả được sắp lại theo khoảng cách sau đó.
SET_HNSW = (
    text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"),
    text("SET LOCAL hnsw.iterative_scan = relaxed_order"),
)


@dataclass(frozen=True)
class ChunkHit:
    message_id: int
    chunk_no: int
    start: int
    end: int
    distance: float


# Bước 1: chỉ bảng message_chunks, ORDER BY khoảng cách LIMIT → planner dùng index HNSW (join với
# messages ngay trong truy vấn này làm planner bỏ index và sắp toàn bộ). RLS và điều kiện model là
# bộ lọc trên chính bảng: iterative scan quét tiếp index cho đủ :k dòng.
NEAREST_CHUNKS = text(
    """
    SELECT c.message_id, c.chunk_no, c.start_pos, c.end_pos, c.cleaner_version,
           c.embedding <=> CAST(:qv AS halfvec(1024)) AS distance
    FROM message_chunks c
    WHERE c.model = :model
    ORDER BY c.embedding <=> CAST(:qv AS halfvec(1024))
    LIMIT :k
    """
)
MAX_CANDIDATES = 20_000  # bằng hnsw.max_scan_tuples mặc định


async def vector_chunks(
    session: AsyncSession,
    vector: Sequence[float],
    filters: MessageFilters,
    *,
    limit: int = VECTOR_CHUNKS,
    exclude_thread_id: int | None = None,
) -> list[ChunkHit]:
    """Đoạn gần vector nhất thỏa bộ lọc (đoạn hợp lệ: model hiện hành, cleaner_version khớp).

    Bước 2 lọc ứng viên bằng messages (bộ lọc, cleaner_version) trong cùng phiên RLS. Bộ lọc loại
    nhiều quá thì lấy thêm ứng viên (×4) tới MAX_CANDIDATES.
    """
    conds, params = filter_conditions(filters)
    if exclude_thread_id is not None:
        conds.append("m.thread_id IS DISTINCT FROM :exclude_thread")
        params["exclude_thread"] = exclude_thread_id
    where = " AND ".join(["m.cleaner_version = p.cv", *conds])
    keep = text(
        f"""
        SELECT p.id FROM unnest(CAST(:ids AS bigint[]), CAST(:cvs AS smallint[])) AS p(id, cv)
        JOIN messages m ON m.id = p.id
        WHERE {where}
        """
    )
    for stmt in SET_HNSW:
        await session.execute(stmt)
    qv = vector_literal(list(vector))
    k = limit
    while True:
        rows = list(
            await session.execute(NEAREST_CHUNKS, {"qv": qv, "model": embed_model(), "k": k})
        )
        ids = sorted({r.message_id for r in rows})
        cvs = {r.message_id: r.cleaner_version for r in rows}
        ok: set[int] = set()
        if ids:
            ok = set(
                await session.scalars(keep, {**params, "ids": ids, "cvs": [cvs[i] for i in ids]})
            )
        hits = [
            ChunkHit(r.message_id, r.chunk_no, r.start_pos, r.end_pos, r.distance)
            for r in rows
            if r.message_id in ok
        ]
        if len(hits) >= limit or len(rows) < k or k >= MAX_CANDIDATES:
            break
        k = min(k * 4, MAX_CANDIDATES)
    hits.sort(key=lambda h: (h.distance, h.message_id, h.chunk_no))
    return hits[:limit]


def best_per_message(hits: Sequence[ChunkHit], limit: int = BRANCH_LIMIT) -> list[ChunkHit]:
    """Đoạn gần nhất của mỗi mail, theo thứ tự khoảng cách."""
    seen: set[int] = set()
    out: list[ChunkHit] = []
    for h in hits:
        if h.message_id not in seen:
            seen.add(h.message_id)
            out.append(h)
            if len(out) >= limit:
                break
    return out


async def keyword_messages(
    session: AsyncSession,
    query: ParsedQuery,
    filters: MessageFilters,
    *,
    limit: int = BRANCH_LIMIT,
) -> list[int]:
    """Mail khớp từ khóa, điểm PGroonga giảm dần (rồi mới hơn trước)."""
    conds, params = filter_conditions(filters)
    where = " AND ".join([MATCH, *conds])
    rows = await session.scalars(
        text(
            f"""
            SELECT m.id FROM messages m
            WHERE {where}
            ORDER BY pgroonga_score(m.tableoid, m.ctid) DESC, m.sent_at DESC NULLS LAST, m.id DESC
            LIMIT :limit
            """
        ),
        {**params, "q": query.groonga, "limit": limit},
    )
    return list(rows)


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: điểm = Σ 1/(k + hạng), hạng từ 1. Bằng điểm: id lớn hơn trước."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], -kv[0]))


async def count_embedding_pending(session: AsyncSession, user_id: int) -> int:
    return int(
        await session.scalar(
            COUNT_PENDING,
            {"u": user_id, "model": embed_model(), "cv": CHUNKER_VERSION, "cap": PENDING_CAP},
        )
        or 0
    )


# chunk: đoạn gần câu tìm kiếm nhất (vị trí lấy từ message_chunks), cắt ngay trong SQL.
DETAILS = text(
    """
    SELECT m.id, m.thread_id, t.message_count AS thread_message_count, m.subject, m.from_name,
           m.from_addr, m.sent_at, m.has_attachments,
           left(coalesce(m.body_clean, m.body_text), :body_cap) AS body,
           CASE WHEN p.e > p.s THEN substr(m.body_clean, p.s + 1, p.e - p.s) END AS chunk
    FROM unnest(CAST(:ids AS bigint[]), CAST(:starts AS integer[]), CAST(:ends AS integer[]))
         AS p(id, s, e)
    JOIN messages m ON m.id = p.id
    LEFT JOIN threads t ON t.id = m.thread_id AND t.user_id = m.user_id
    """
)
SNIPPET_BODY_CAP = 300_000


async def hybrid_search(
    session: AsyncSession,
    vector: Sequence[float],
    user_id: int,
    keyword_query: ParsedQuery | None,
    filters: MessageFilters,
    *,
    offset: int = 0,
    limit: int = 50,
) -> SearchResult:
    """Spec 19 mục 4. vector: câu tìm kiếm đã embed TRƯỚC khi mở phiên DB (không giữ transaction
    trong lúc chờ ml-service). keyword_query: theo cú pháp ô tìm kiếm (None: không có)."""
    started = time.monotonic()
    qv = vector
    vector_hits = best_per_message(await vector_chunks(session, qv, filters))
    keyword_ids = await keyword_messages(session, keyword_query, filters) if keyword_query else []
    fused = rrf([[h.message_id for h in vector_hits], keyword_ids])[:MAX_RESULTS]
    page = [mid for mid, _ in fused[offset : offset + limit]]
    by_vector = {h.message_id: h for h in vector_hits}
    by_keyword = set(keyword_ids)
    keywords = list(keyword_query.keywords) if keyword_query else []

    rows: dict[int, Any] = {}
    if page:
        spans = [by_vector.get(mid) for mid in page]
        params = {
            "ids": page,
            "starts": [h.start if h else 0 for h in spans],
            "ends": [h.end if h else 0 for h in spans],
            "body_cap": SNIPPET_BODY_CAP,
        }
        for r in await session.execute(DETAILS, params):
            rows[r.id] = r
    folders = await folders_of(session, page)
    pending = await count_embedding_pending(session, user_id)

    def build() -> list[SearchHit]:
        items: list[SearchHit] = []
        for mid in page:
            r = rows.get(mid)
            if r is None:  # mail vừa bị xóa giữa hai truy vấn
                continue
            hit = by_vector.get(mid)
            snip = snippet(r.chunk if r.chunk else r.body, keywords)
            match = (
                "both"
                if hit is not None and mid in by_keyword
                else "semantic"
                if hit is not None
                else "keyword"
            )
            items.append(
                SearchHit(
                    message_id=r.id,
                    thread_id=r.thread_id,
                    thread_message_count=r.thread_message_count,
                    subject=highlight(r.subject, keywords),
                    from_name=r.from_name,
                    from_addr=r.from_addr,
                    sent_at=r.sent_at,
                    has_attachments=r.has_attachments,
                    snippet=snip,
                    folders=folders.get(r.id, []),
                    match=match,
                )
            )
        return items

    items = await run_in_threadpool(build)
    log.info(
        "search user=%s mode=hybrid total=%d ms=%d",
        user_id,
        len(fused),
        (time.monotonic() - started) * 1000,
    )
    return SearchResult(
        total=len(fused),
        total_capped=False,
        offset=offset,
        limit=limit,
        keywords=keywords,
        items=items,
        mode="hybrid",
        embedding_pending=pending,
    )
