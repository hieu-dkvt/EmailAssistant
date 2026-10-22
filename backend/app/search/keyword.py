"""Search từ khóa trên subject + body_clean bằng PGroonga, kèm bộ lọc (spec 14 mục 5).

Chạy trong user_session (app_api + RLS): lọc quyền nằm trong cùng truy vấn, không thêm điều kiện
user_id tường minh (khi có chia sẻ theo dự án, RLS tự mở rộng đúng phạm vi). Mọi giá trị đi vào SQL
bằng tham số bind; chuỗi Groonga do query.parse_query dựng. Không log chuỗi tìm kiếm.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.search.highlight import HIGHLIGHT_LIMIT, highlight, snippet
from app.search.query import ParsedQuery
from app.web_schemas import FolderRef, SearchHit, SearchResult

log = logging.getLogger("app.search")

type SortOrder = Literal["date", "relevance"]

# Biểu thức phải giống hệt index ix_messages_search (migration 0012) thì planner mới dùng index.
MATCH = "ARRAY[m.subject, m.body_clean] &@~ :q"
ORDER_BY_DATE = "m.sent_at DESC NULLS LAST, m.id DESC"
ORDER_BY_SCORE = "pgroonga_score(m.tableoid, m.ctid) DESC, " + ORDER_BY_DATE
TOTAL_CAP = 10_000  # đếm tối đa; nhiều hơn thì total_capped = true ("hơn 10.000 kết quả")


@dataclass(frozen=True)
class MessageFilters:
    folder_id: int | None = None
    sender: str | None = None
    sent_after: datetime | None = None
    sent_before: datetime | None = None
    has_attachments: bool | None = None


def like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def filter_conditions(f: MessageFilters, alias: str = "m") -> tuple[list[str], dict[str, Any]]:
    """Điều kiện SQL mức mail (alias: bảng messages) và tham số. Dùng chung với danh sách thread."""
    conds: list[str] = []
    params: dict[str, Any] = {}
    if f.folder_id is not None:
        conds.append(
            "EXISTS (SELECT 1 FROM message_ownership o WHERE o.message_id = "
            f"{alias}.id AND o.user_id = {alias}.user_id AND o.source_id = :folder_id "
            "AND NOT o.is_deleted)"
        )
        params["folder_id"] = f.folder_id
    if f.sender:
        conds.append(
            f"({alias}.from_addr ILIKE :sender ESCAPE '\\' "
            f"OR {alias}.from_name ILIKE :sender ESCAPE '\\')"
        )
        params["sender"] = like_pattern(f.sender)
    if f.sent_after is not None:
        conds.append(f"{alias}.sent_at >= :sent_after")
        params["sent_after"] = f.sent_after
    if f.sent_before is not None:
        conds.append(f"{alias}.sent_at < :sent_before")
        params["sent_before"] = f.sent_before
    if f.has_attachments is not None:
        conds.append(f"{alias}.has_attachments = :has_attachments")
        params["has_attachments"] = f.has_attachments
    return conds, params


FOLDERS_OF = text(
    """
    SELECT o.message_id, s.id, s.folder_path
    FROM message_ownership o
    JOIN mail_sources s ON s.id = o.source_id AND s.user_id = o.user_id
    WHERE o.message_id = ANY(CAST(:ids AS bigint[])) AND NOT o.is_deleted
    ORDER BY s.folder_path
    """
)


async def folders_of(session: AsyncSession, message_ids: list[int]) -> dict[int, list[FolderRef]]:
    out: dict[int, list[FolderRef]] = {}
    if not message_ids:
        return out
    for r in await session.execute(FOLDERS_OF, {"ids": message_ids}):
        out.setdefault(r.message_id, []).append(FolderRef(id=r.id, path=r.folder_path))
    return out


async def search_messages(
    session: AsyncSession,
    user_id: int,
    query: ParsedQuery | None,
    filters: MessageFilters,
    *,
    sort: SortOrder = "date",
    offset: int = 0,
    limit: int = 50,
) -> SearchResult:
    started = time.monotonic()
    conds, params = filter_conditions(filters)
    if query is not None:
        conds.insert(0, MATCH)
        params["q"] = query.groonga
    where = " AND ".join(conds) if conds else "true"
    order = ORDER_BY_SCORE if sort == "relevance" and query is not None else ORDER_BY_DATE
    # Đếm tối đa TOTAL_CAP + 1 mail: từ khóa phổ biến khớp hàng trăm nghìn mail, đếm hết tốn gần
    # bằng cả truy vấn trang; phân trang cũng chỉ tới offset 10.000.
    counted = await session.scalar(
        text(f"SELECT count(*) FROM (SELECT 1 FROM messages m WHERE {where} LIMIT :cap) c"),
        {**params, "cap": TOTAL_CAP + 1},
    )
    total = min(counted or 0, TOTAL_CAP)
    score = "pgroonga_score(m.tableoid, m.ctid)" if order is ORDER_BY_SCORE else "0"
    # Sắp và cắt trang trên cột hẹp trước (subquery có LIMIT không bị gộp ra ngoài), rồi mới lấy
    # nội dung và nối threads cho đúng các mail của trang. Nếu không, planner nối threads và đọc
    # cột nội dung cho mọi mail khớp (đo: 218.000 dòng, vài giây).
    rows = (
        await session.execute(
            text(
                f"""
                SELECT m.id, m.thread_id, t.message_count AS thread_message_count, m.subject,
                       m.from_name, m.from_addr, m.sent_at, m.has_attachments,
                       left(coalesce(m.body_clean, m.body_text), :body_cap) AS body
                FROM (
                    SELECT m.id, m.sent_at, {score} AS score
                    FROM messages m
                    WHERE {where}
                    ORDER BY {order}
                    LIMIT :limit OFFSET :offset
                ) p
                JOIN messages m ON m.id = p.id
                LEFT JOIN threads t ON t.id = m.thread_id AND t.user_id = m.user_id
                ORDER BY p.score DESC, p.sent_at DESC NULLS LAST, p.id DESC
                """
            ),
            {**params, "body_cap": HIGHLIGHT_LIMIT, "limit": limit, "offset": offset},
        )
    ).all()
    keywords = list(query.keywords) if query else []
    folders = await folders_of(session, [r.id for r in rows])

    def build() -> list[SearchHit]:
        return [
            SearchHit(
                message_id=r.id,
                thread_id=r.thread_id,
                thread_message_count=r.thread_message_count,
                subject=highlight(r.subject, keywords),
                from_name=r.from_name,
                from_addr=r.from_addr,
                sent_at=r.sent_at,
                has_attachments=r.has_attachments,
                snippet=snippet(r.body, keywords),
                folders=folders.get(r.id, []),
            )
            for r in rows
        ]

    # Đoạn trích quét tới 300.000 ký tự mỗi mail: chạy ở thread riêng, không chặn event loop.
    items = await run_in_threadpool(build)
    log.info("search user=%s total=%d ms=%d", user_id, total, (time.monotonic() - started) * 1000)
    return SearchResult(
        total=total,
        total_capped=(counted or 0) > TOTAL_CAP,
        offset=offset,
        limit=limit,
        keywords=keywords,
        items=items,
    )
