"""Bảng cho bước đồng bộ mail: users, mail_sources, messages, message_ownership.

RLS, policy và GRANT ở migration 0002.

Revision ID: 0001
Revises:
Create Date: 2026-10-17 06:25:48.960427
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("message_id_hash", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("from_addr", sa.Text(), nullable=True),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column(
            "to_addrs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "cc_addrs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_tz_offset_min", sa.SmallInteger(), nullable=True),
        sa.Column("in_reply_to", sa.Text(), nullable=True),
        sa.Column(
            "references_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("thread_index", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("body_from_html", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("has_attachments", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("message_id_hash", name=op.f("uq_messages_message_id_hash")),
    )
    op.create_index(op.f("ix_messages_from_addr"), "messages", ["from_addr"], unique=False)
    op.create_index(op.f("ix_messages_sent_at"), "messages", ["sent_at"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('admin', 'pm', 'member')", name=op.f("ck_users_role")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lower")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "mail_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("folder_path", sa.Text(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("last_offset", sa.BigInteger(), nullable=True),
        sa.Column("last_mtime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('local', 'imap_cache')", name=op.f("ck_mail_sources_kind")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mail_sources_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_sources")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_mail_sources_id_user_id")),
        sa.UniqueConstraint(
            "user_id", "folder_path", name=op.f("uq_mail_sources_user_id_folder_path")
        ),
    )
    op.create_table(
        "message_ownership",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("mbox_offset", sa.BigInteger(), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_replied", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_flagged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_ownership_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["mail_sources.id", "mail_sources.user_id"],
            name="fk_message_ownership_source_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id", "source_id", name=op.f("pk_message_ownership")),
    )
    op.create_index(
        "ix_message_ownership_user_id_message_id",
        "message_ownership",
        ["user_id", "message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_message_ownership_user_id_message_id", table_name="message_ownership")
    op.drop_table("message_ownership")
    op.drop_table("mail_sources")
    op.drop_table("users")
    op.drop_index(op.f("ix_messages_sent_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_from_addr"), table_name="messages")
    op.drop_table("messages")
