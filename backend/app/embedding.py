"""Embedding mail: cắt đoạn body_clean, gọi ml-service, ghi message_chunks (spec 18).

Chạy bằng role app_worker (BYPASSRLS): MỌI truy vấn có user_id = :u. Chỉ lưu vị trí đoạn, không
chép nội dung mail. Log chỉ có id, số lượng, thời gian.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import embed_model
from app.ml_client import MlClient, vector_literal

log = logging.getLogger("app.embedding")

CHUNKER_VERSION = 1
# bge-m3 trên mail giả (Nhật/Việt/Anh): 0,37 token/ký tự (p90 0,41, cao nhất 0,57). 800 ký tự +
# subject < 512 token (ML_MAX_SEQ_LENGTH): không bị cắt.
CHUNK_CHARS = 800
CHUNK_OVERLAP = 100
MAX_CHUNKS = 16
EMBED_BATCH = 16  # số mail mỗi lô (một transaction)
EMBED_JOB_SECONDS = 10.0  # một job embed_user chạy tối đa ngần này rồi nhường worker

_SENTENCE_ENDS = ("。", "．", "！", "？", ". ", "! ", "? ")


@dataclass(frozen=True)
class Chunk:
    no: int
    start: int
    end: int


def split_chunks(
    body: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP, limit: int = MAX_CHUNKS
) -> list[Chunk]:
    """Cắt body thành đoạn ≤ size ký tự, đoạn sau lặp lại overlap ký tự cuối của đoạn trước.

    Ưu tiên cắt ở cuối dòng rồi cuối câu trong nửa sau của đoạn. Rỗng → một đoạn [0, 0).
    """
    n = len(body)
    if n <= size:
        return [Chunk(0, 0, n)]
    chunks: list[Chunk] = []
    start = 0
    while start < n and len(chunks) < limit:
        end = min(start + size, n)
        if end < n:
            lo = start + size // 2
            cut = body.rfind("\n", lo, end)
            if cut != -1:
                end = cut + 1
            else:
                # Sau dấu kết câu gần cuối đoạn nhất (dấu Latin tính cả khoảng trắng theo sau).
                best = max((body.rfind(s, lo, end) + len(s), s) for s in _SENTENCE_ENDS)
                if best[0] >= lo + len(best[1]):
                    end = best[0]
        chunks.append(Chunk(len(chunks), start, end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


MAX_SUBJECT_CHARS = 300  # subject nhận tới 10.000 ký tự: cắt để text luôn dưới giới hạn ml-service


def embed_text(subject: str | None, piece: str) -> str:
    """Dạng chung của text embed và text rerank: 300 ký tự đầu subject, xuống dòng, đoạn."""
    return f"{(subject or '')[:MAX_SUBJECT_CHARS]}\n{piece}".strip()


def embed_input(subject: str | None, body: str, chunk: Chunk) -> str:
    """Text gửi đi embed: subject giúp đoạn ngắn có ngữ cảnh."""
    return embed_text(subject, body[chunk.start : chunk.end])


# Mail cần embed: đã làm sạch, chưa có đoạn 0 đúng model / cách cắt / bản body_clean hiện hành.
_NEEDS_EMBED = """
    m.body_clean IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM message_chunks c
        WHERE c.user_id = m.user_id AND c.message_id = m.id AND c.chunk_no = 0
          AND c.model = :model AND c.chunker_version = :cv
          AND c.cleaner_version = m.cleaner_version
    )
"""
PENDING_BATCH = text(
    f"""
    SELECT m.id, m.subject, left(m.body_clean, :max_chars) AS body, m.cleaner_version
    FROM messages m
    WHERE m.user_id = :u AND {_NEEDS_EMBED}
    ORDER BY m.sent_at DESC NULLS LAST, m.id DESC
    LIMIT :n
    """
)
PENDING_USERS = text(
    f"""
    SELECT u.id FROM users u
    WHERE EXISTS (SELECT 1 FROM messages m WHERE m.user_id = u.id AND {_NEEDS_EMBED})
    ORDER BY u.id
    """
)
COUNT_PENDING = text(
    f"SELECT count(*) FROM (SELECT 1 FROM messages m WHERE m.user_id = :u AND {_NEEDS_EMBED} "
    "LIMIT :cap) p"
)
DELETE_CHUNKS = text(
    "DELETE FROM message_chunks WHERE user_id = :u AND message_id = ANY(CAST(:ids AS bigint[]))"
)
INSERT_CHUNKS = text(
    """
    INSERT INTO message_chunks (user_id, message_id, chunk_no, start_pos, end_pos, model,
                                chunker_version, cleaner_version, embedding)
    SELECT :u, c.message_id, c.chunk_no, c.start_pos, c.end_pos, :model, :cv, c.cleaner_version,
           CAST(c.embedding AS halfvec(1024))
    FROM unnest(CAST(:mids AS bigint[]), CAST(:nos AS smallint[]), CAST(:starts AS integer[]),
                CAST(:ends AS integer[]), CAST(:cvs AS smallint[]), CAST(:vecs AS text[]))
         AS c(message_id, chunk_no, start_pos, end_pos, cleaner_version, embedding)
    """
)


@dataclass
class EmbedStats:
    messages: int = 0
    chunks: int = 0
    more: bool = False  # còn mail chờ embed (job tự đẩy tiếp)


def _params(user_id: int) -> dict[str, object]:
    return {"u": user_id, "model": embed_model(), "cv": CHUNKER_VERSION}


async def pending_users(engine: AsyncEngine) -> list[int]:
    async with AsyncSession(engine) as s:
        return list(await s.scalars(PENDING_USERS, {"model": embed_model(), "cv": CHUNKER_VERSION}))


async def count_pending(engine: AsyncEngine, user_id: int, cap: int = 10_001) -> int:
    async with AsyncSession(engine) as s:
        return int(await s.scalar(COUNT_PENDING, {**_params(user_id), "cap": cap}) or 0)


async def embed_batch(engine: AsyncEngine, ml: MlClient, user_id: int, n: int) -> EmbedStats:
    """Embed tối đa n mail mới nhất đang chờ; một transaction. Trả số mail/đoạn đã ghi."""
    params = _params(user_id)
    async with AsyncSession(engine) as s, s.begin():
        rows = (
            await s.execute(
                PENDING_BATCH, {**params, "max_chars": MAX_CHUNKS * CHUNK_CHARS, "n": n}
            )
        ).all()
        if not rows:
            return EmbedStats()
        inputs: list[str] = []
        cols: dict[str, list[object]] = {k: [] for k in ("mids", "nos", "starts", "ends", "cvs")}
        for r in rows:
            for c in split_chunks(r.body):
                inputs.append(embed_input(r.subject, r.body, c))
                cols["mids"].append(r.id)
                cols["nos"].append(c.no)
                cols["starts"].append(c.start)
                cols["ends"].append(c.end)
                cols["cvs"].append(r.cleaner_version)
        # Gọi ml-service khi đang giữ transaction: lô nhỏ (EMBED_BATCH mail), chỉ đọc khóa dòng
        # của chính bảng message_chunks; làm sạch lại giữa chừng thì lần sau embed lại.
        vectors = await ml.embed(inputs)
        await s.execute(DELETE_CHUNKS, {"u": user_id, "ids": [r.id for r in rows]})
        await s.execute(
            INSERT_CHUNKS, {**params, **cols, "vecs": [vector_literal(v) for v in vectors]}
        )
    return EmbedStats(messages=len(rows), chunks=len(inputs), more=len(rows) == n)


async def embed_user(
    engine: AsyncEngine,
    ml: MlClient,
    user_id: int,
    *,
    budget_seconds: float = EMBED_JOB_SECONDS,
    batch: int = EMBED_BATCH,
) -> EmbedStats:
    """Embed theo lô tới khi hết mail chờ hoặc hết thời gian; more = còn mail chờ."""
    started = time.monotonic()
    total = EmbedStats()
    while True:
        stats = await embed_batch(engine, ml, user_id, batch)
        total.messages += stats.messages
        total.chunks += stats.chunks
        total.more = stats.more
        if not stats.more or time.monotonic() - started >= budget_seconds:
            break
    if total.messages:
        log.info(
            "embed user=%s messages=%d chunks=%d more=%s ms=%d",
            user_id,
            total.messages,
            total.chunks,
            total.more,
            (time.monotonic() - started) * 1000,
        )
    return total
