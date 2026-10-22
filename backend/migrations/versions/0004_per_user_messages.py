"""Mỗi người dùng có bản messages riêng (không dùng chung mail giữa người dùng).

Quyết định 2026-10-17: dùng chung một dòng messages theo message_id_hash cho phép người biết
Message-ID (vd. thấy trong header References của reply) gửi mail giả cùng Message-ID để được
gắn ownership và đọc bản thật. Giờ:
- messages.user_id; khóa chống trùng là (user_id, message_id_hash).
- message_ownership trỏ tới messages bằng FK kép (message_id, user_id): ownership chỉ gắn
  được vào mail của đúng người dùng.
- Policy SELECT của messages: user_id = app_user_id() (không cần EXISTS qua ownership).
- Index bắt đầu bằng user_id vì mọi truy vấn đều lọc theo người dùng.

upgrade chỉ chạy khi messages rỗng: không suy ra được dòng cũ (dùng chung) thuộc về ai.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17 09:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_MESSAGES_POLICY = """
    CREATE POLICY messages_owner_select ON messages FOR SELECT
    USING (EXISTS (
        SELECT 1 FROM message_ownership mo
        WHERE mo.message_id = messages.id AND mo.user_id = app_user_id()
    ))
"""


def upgrade() -> None:
    count = op.get_bind().scalar(sa.text("select count(*) from messages"))
    if count:
        raise RuntimeError(
            f"messages có {count} dòng: migration 0004 chỉ chạy trên bảng rỗng "
            "(không xác định được mail dùng chung thuộc người dùng nào)."
        )

    op.add_column("messages", sa.Column("user_id", sa.BigInteger(), nullable=False))
    op.create_foreign_key(
        "fk_messages_user_id_users", "messages", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("uq_messages_message_id_hash", "messages", type_="unique")
    op.create_unique_constraint(
        "uq_messages_user_id_message_id_hash", "messages", ["user_id", "message_id_hash"]
    )
    op.create_unique_constraint("uq_messages_id_user_id", "messages", ["id", "user_id"])

    op.drop_index("ix_messages_from_addr", table_name="messages")
    op.drop_index("ix_messages_sent_at", table_name="messages")
    op.create_index("ix_messages_user_id_sent_at", "messages", ["user_id", "sent_at"])
    op.create_index("ix_messages_user_id_from_addr", "messages", ["user_id", "from_addr"])

    op.drop_constraint(
        "fk_message_ownership_message_id_messages", "message_ownership", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_message_ownership_message_user",
        "message_ownership",
        "messages",
        ["message_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )

    op.execute("DROP POLICY messages_owner_select ON messages")
    op.execute(
        "CREATE POLICY messages_owner_select ON messages FOR SELECT USING (user_id = app_user_id())"
    )


def downgrade() -> None:
    # Lỗi nếu có hai người dùng cùng một message_id_hash (khóa toàn cục bị vi phạm).
    op.execute("DROP POLICY messages_owner_select ON messages")
    op.execute(OLD_MESSAGES_POLICY)

    op.drop_constraint("fk_message_ownership_message_user", "message_ownership", type_="foreignkey")
    op.create_foreign_key(
        "fk_message_ownership_message_id_messages",
        "message_ownership",
        "messages",
        ["message_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index("ix_messages_user_id_from_addr", table_name="messages")
    op.drop_index("ix_messages_user_id_sent_at", table_name="messages")
    op.create_index("ix_messages_sent_at", "messages", ["sent_at"])
    op.create_index("ix_messages_from_addr", "messages", ["from_addr"])

    op.drop_constraint("uq_messages_id_user_id", "messages", type_="unique")
    op.drop_constraint("uq_messages_user_id_message_id_hash", "messages", type_="unique")
    op.create_unique_constraint("uq_messages_message_id_hash", "messages", ["message_id_hash"])
    op.drop_constraint("fk_messages_user_id_users", "messages", type_="foreignkey")
    op.drop_column("messages", "user_id")
