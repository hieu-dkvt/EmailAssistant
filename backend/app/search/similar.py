"""Gợi ý thread liên quan bằng embedding đã lưu (lớp 4 gom thread, spec 23).

Chạy trong user_session (RLS); không gọi ml-service (dùng vector có sẵn trong message_chunks).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import embed_model
from app.search.keyword import MessageFilters
from app.search.semantic import vector_chunks
from app.web_schemas import SimilarThread, SimilarThreads

SEEDS = 5  # số mail mới nhất của thread dùng làm hạt giống
NEIGHBOURS = 30  # đoạn gần nhất cho mỗi hạt giống
SIMILAR_MIN = 0.80  # ngưỡng cosine (spec 23 mục 8)
MAX_ITEMS = 5

THREAD_EXISTS = text("SELECT 1 FROM threads WHERE id = :t")
SEED_VECTORS = text(
    """
    SELECT c.embedding
    FROM messages m
    JOIN message_chunks c ON c.message_id = m.id AND c.user_id = m.user_id AND c.chunk_no = 0
    WHERE m.thread_id = :t AND c.model = :model AND c.cleaner_version = m.cleaner_version
    ORDER BY m.sent_at DESC NULLS LAST, m.id DESC
    LIMIT :seeds
    """
)
THREAD_OF = text(
    "SELECT id, thread_id FROM messages WHERE id = ANY(CAST(:ids AS bigint[])) "
    "AND thread_id IS NOT NULL"
)
THREAD_INFO = text(
    """
    SELECT t.id, t.message_count, t.last_message_at,
           (SELECT m.subject FROM messages m WHERE m.thread_id = t.id AND m.user_id = t.user_id
            ORDER BY m.sent_at NULLS LAST, m.id LIMIT 1) AS subject
    FROM threads t WHERE t.id = ANY(CAST(:ids AS bigint[]))
    """
)


async def similar_threads(session: AsyncSession, thread_id: int) -> SimilarThreads | None:
    """None: không có thread (hoặc không phải của người dùng)."""
    if await session.scalar(THREAD_EXISTS, {"t": thread_id}) is None:
        return None
    model = embed_model()
    seeds = list(
        await session.scalars(SEED_VECTORS, {"t": thread_id, "model": model, "seeds": SEEDS})
    )
    if not seeds:
        return SimilarThreads(items=[], pending=True)
    best: dict[int, tuple[float, int]] = {}  # thread → (điểm, mail đại diện)
    for raw in seeds:
        vec = [float(x) for x in str(raw).strip("[]").split(",")]
        hits = await vector_chunks(
            session, vec, MessageFilters(), limit=NEIGHBOURS, exclude_thread_id=thread_id
        )
        threads = {
            r.id: r.thread_id
            for r in await session.execute(THREAD_OF, {"ids": [h.message_id for h in hits]})
        }
        for h in hits:
            score = 1.0 - float(h.distance)
            tid = threads.get(h.message_id)
            if tid is not None and score >= SIMILAR_MIN and score > best.get(tid, (-1.0, 0))[0]:
                best[tid] = (score, h.message_id)
    top = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))[:MAX_ITEMS]
    if not top:
        return SimilarThreads(items=[], pending=False)
    info = {r.id: r for r in await session.execute(THREAD_INFO, {"ids": [t for t, _ in top]})}
    items = [
        SimilarThread(
            thread_id=tid,
            subject=info[tid].subject,
            message_count=info[tid].message_count,
            last_message_at=info[tid].last_message_at,
            score=round(score, 4),
            message_id=mid,
        )
        for tid, (score, mid) in top
        if tid in info
    ]
    return SimilarThreads(items=items, pending=False)
