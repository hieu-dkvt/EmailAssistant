"""Token collector và quyền cho ingest / xóa mail.

- api_tokens: token collector (chỉ lưu SHA-256). app_api không có quyền gì trên bảng;
  xác thực qua hàm SECURITY DEFINER auth_token_user (search_path cố định, chỉ app_api gọi được).
- Quyền ghi cho ingest (spec ingest API), theo điều kiện từ review bảo mật 0004:
  INSERT theo cột (không có id), không UPDATE messages, ON CONFLICT DO NOTHING ở tầng API;
  message_ownership chỉ UPDATE mbox_offset / last_seen_at / is_deleted / cờ;
  mail_sources cấp lại theo cột (không có id; UPDATE không có user_id).
- Xóa mail của folder (spec xóa mail): DELETE messages của chính mình.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MESSAGE_INSERT_COLUMNS = (
    "user_id, message_id_hash, message_id, subject, from_addr, from_name, to_addrs, cc_addrs, "
    "sent_at, sent_tz_offset_min, in_reply_to, references_ids, thread_index, body_text, "
    "body_from_html, has_attachments, attachments, parser_version"
)
OWNERSHIP_INSERT_COLUMNS = (
    "message_id, source_id, user_id, mbox_offset, is_read, is_replied, is_flagged"
)
OWNERSHIP_UPDATE_COLUMNS = "mbox_offset, last_seen_at, is_deleted"
SOURCE_INSERT_COLUMNS = (
    "user_id, kind, account, folder_path, file_size, last_offset, last_mtime, last_scan_at"
)
SOURCE_UPDATE_COLUMNS = "kind, account, file_size, last_offset, last_mtime, last_scan_at"


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("label", sa.Text(), server_default=sa.text("'collector'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_api_tokens_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )
    op.execute("REVOKE ALL ON api_tokens FROM PUBLIC")
    op.execute(
        """
        CREATE FUNCTION auth_token_user(p_token_hash bytea) RETURNS bigint
        LANGUAGE sql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            UPDATE public.api_tokens SET last_used_at = now()
            WHERE token_hash = p_token_hash AND revoked_at IS NULL
            RETURNING user_id
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_token_user(bytea) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_token_user(bytea) TO app_api")

    # messages: ingest + xóa mail của chính mình
    op.execute(f"GRANT INSERT ({MESSAGE_INSERT_COLUMNS}) ON messages TO app_api")
    op.execute(
        "CREATE POLICY messages_owner_insert ON messages FOR INSERT "
        "WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT DELETE ON messages TO app_api")
    op.execute(
        "CREATE POLICY messages_owner_delete ON messages FOR DELETE USING (user_id = app_user_id())"
    )

    # message_ownership: tạo và cập nhật vị trí/trạng thái (cờ đã cấp ở 0003)
    op.execute(f"GRANT INSERT ({OWNERSHIP_INSERT_COLUMNS}) ON message_ownership TO app_api")
    op.execute(f"GRANT UPDATE ({OWNERSHIP_UPDATE_COLUMNS}) ON message_ownership TO app_api")

    # mail_sources: cấp lại theo cột (không có id; UPDATE không có user_id)
    op.execute("REVOKE INSERT, UPDATE ON mail_sources FROM app_api")
    op.execute(f"GRANT INSERT ({SOURCE_INSERT_COLUMNS}) ON mail_sources TO app_api")
    op.execute(f"GRANT UPDATE ({SOURCE_UPDATE_COLUMNS}) ON mail_sources TO app_api")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE ({SOURCE_UPDATE_COLUMNS}) ON mail_sources FROM app_api")
    op.execute(f"REVOKE INSERT ({SOURCE_INSERT_COLUMNS}) ON mail_sources FROM app_api")
    op.execute("GRANT INSERT, UPDATE ON mail_sources TO app_api")

    op.execute(f"REVOKE UPDATE ({OWNERSHIP_UPDATE_COLUMNS}) ON message_ownership FROM app_api")
    op.execute(f"REVOKE INSERT ({OWNERSHIP_INSERT_COLUMNS}) ON message_ownership FROM app_api")

    op.execute("DROP POLICY messages_owner_delete ON messages")
    op.execute("REVOKE DELETE ON messages FROM app_api")
    op.execute("DROP POLICY messages_owner_insert ON messages")
    op.execute(f"REVOKE INSERT ({MESSAGE_INSERT_COLUMNS}) ON messages FROM app_api")

    op.execute("DROP FUNCTION auth_token_user(bytea)")
    op.drop_table("api_tokens")
