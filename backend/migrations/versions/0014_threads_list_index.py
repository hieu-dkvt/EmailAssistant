"""Index cho danh sách thread của web (spec 2026-10-18-thread-api-design.md mục 4.2).

Danh sách sắp theo `last_message_at DESC NULLS LAST, id DESC`. Index cũ (user_id, last_message_at)
đọc ngược cho ra DESC NULLS FIRST, nên planner phải sắp toàn bộ thread của người dùng (đo trên
100.000 thread tổng hợp: khoảng 430 ms). Index mới khớp đúng thứ tự, thay index cũ (vẫn dùng được
cho điều kiện user_id). CONCURRENTLY: bảng đã có dữ liệu, migration riêng như 0007.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-19 10:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_threads_list ON threads "
            "(user_id, last_message_at DESC NULLS LAST, id DESC)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_threads_user_id_last_message_at")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_threads_user_id_last_message_at "
            "ON threads (user_id, last_message_at)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_threads_list")
