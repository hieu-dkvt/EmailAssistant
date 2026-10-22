"""Làm sạch nội dung và gom thread (spec 2026-10-17-content-cleaning, 2026-10-17-threading).

- messages: body_clean, clean_cuts, cleaner_version; thread_id, thread_parent_id, thread_method.
  Chỉ app_worker ghi (app_api không có UPDATE trên messages, INSERT theo cột của 0005 không
  gồm các cột này).
- threads: thread của một người dùng. FK kép (thread_id, user_id) từ messages: mail chỉ nằm
  được trong thread của chính người đó. ON DELETE SET NULL (thread_id): xóa thread không xóa
  user_id của mail.
- thread_overrides: gộp/tách tay, tham chiếu mail (id thread có thể đổi khi gom lại).
- RLS: SELECT của chính người dùng; app_api chỉ SELECT; app_worker ghi. app_worker cấp số id
  thread hàng loạt bằng nextval (USAGE trên sequence).
- Index của các cột mới trên messages (bảng đã có dữ liệu) tạo CONCURRENTLY ở 0007.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

THREAD_METHODS = "'root', 'references', 'thread_index', 'subject', 'merge'"
NEW_TABLES = ("threads", "thread_overrides")


def upgrade() -> None:
    op.add_column("messages", sa.Column("body_clean", sa.Text(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("clean_cuts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("messages", sa.Column("cleaner_version", sa.SmallInteger(), nullable=True))

    op.create_table(
        "threads",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("subject_norm", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("threader_version", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_threads_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_threads"),
        sa.UniqueConstraint("id", "user_id", name="uq_threads_id_user_id"),
    )
    op.create_index("ix_threads_user_id_last_message_at", "threads", ["user_id", "last_message_at"])

    op.add_column("messages", sa.Column("thread_id", sa.BigInteger(), nullable=True))
    op.add_column("messages", sa.Column("thread_parent_id", sa.BigInteger(), nullable=True))
    op.add_column("messages", sa.Column("thread_method", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_messages_thread_method"), "messages", f"thread_method IN ({THREAD_METHODS})"
    )
    op.create_foreign_key(
        "fk_messages_thread_user",
        "messages",
        "threads",
        ["thread_id", "user_id"],
        ["id", "user_id"],
        ondelete="SET NULL (thread_id)",
    )
    op.create_foreign_key(
        "fk_messages_thread_parent_user",
        "messages",
        "messages",
        ["thread_parent_id", "user_id"],
        ["id", "user_id"],
        ondelete="SET NULL (thread_parent_id)",
    )

    op.create_table(
        "thread_overrides",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("target_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("action IN ('split', 'merge')", name=op.f("ck_thread_overrides_action")),
        sa.CheckConstraint(
            "(action = 'merge') = (target_message_id IS NOT NULL)",
            name=op.f("ck_thread_overrides_target"),
        ),
        sa.CheckConstraint(
            "target_message_id <> message_id", name=op.f("ck_thread_overrides_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_thread_overrides_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_thread_overrides_message_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_thread_overrides_target_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_thread_overrides"),
    )
    op.create_index("ix_thread_overrides_user_id", "thread_overrides", ["user_id"])

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_owner_select ON {table} FOR SELECT "
            "USING (user_id = app_user_id())"
        )
        op.execute(f"GRANT SELECT ON {table} TO app_api")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_worker")
    op.execute("GRANT USAGE ON SEQUENCE threads_id_seq TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE USAGE ON SEQUENCE threads_id_seq FROM app_worker")
    for table in reversed(NEW_TABLES):
        op.execute(f"REVOKE ALL ON {table} FROM app_api, app_worker")
        op.execute(f"DROP POLICY {table}_owner_select ON {table}")
    op.drop_index("ix_thread_overrides_user_id", table_name="thread_overrides")
    op.drop_table("thread_overrides")
    op.drop_constraint("fk_messages_thread_parent_user", "messages", type_="foreignkey")
    op.drop_constraint("fk_messages_thread_user", "messages", type_="foreignkey")
    op.drop_constraint(op.f("ck_messages_thread_method"), "messages", type_="check")
    op.drop_column("messages", "thread_method")
    op.drop_column("messages", "thread_parent_id")
    op.drop_column("messages", "thread_id")
    op.drop_index("ix_threads_user_id_last_message_at", table_name="threads")
    op.drop_table("threads")
    op.drop_column("messages", "cleaner_version")
    op.drop_column("messages", "clean_cuts")
    op.drop_column("messages", "body_clean")
