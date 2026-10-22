"""Index cho các cột gom thread của messages, tạo CONCURRENTLY (bảng đã có dữ liệu).

Tách khỏi 0006: CREATE INDEX CONCURRENTLY chạy ngoài transaction, nên đặt riêng một migration
(env.py chạy mỗi migration trong transaction riêng). IF NOT EXISTS: chạy lại sau khi bị ngắt
giữa chừng không lỗi.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17 16:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_messages_user_id_thread_id",
            "messages",
            ["user_id", "thread_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_messages_thread_parent_id",
            "messages",
            ["thread_parent_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    op.drop_index("ix_messages_thread_parent_id", table_name="messages")
    op.drop_index("ix_messages_user_id_thread_id", table_name="messages")
