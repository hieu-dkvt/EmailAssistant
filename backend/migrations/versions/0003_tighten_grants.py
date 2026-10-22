"""Siết quyền sau review bảo mật.

- app_api không INSERT/UPDATE message_ownership: nếu được, người dùng tự gắn mình vào mail
  bất kỳ (message_id tăng dần, đoán được) rồi đọc toàn văn qua policy SELECT của messages.
  Ownership chỉ được tạo bởi hàm SECURITY DEFINER của bước ingest. app_api chỉ còn sửa
  được cờ is_read / is_replied / is_flagged trên dòng của chính mình (RLS).
- app_user_id() PARALLEL SAFE: hàm nằm trong mọi policy; mặc định PARALLEL UNSAFE khiến mọi
  truy vấn trên bảng mail không dùng được parallel plan. Gọi pg_catalog.current_setting
  tường minh. Không thêm SET search_path vì SET làm hàm không inline được (mất index).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17 08:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAG_COLUMNS = "is_read, is_replied, is_flagged"


def upgrade() -> None:
    op.execute("REVOKE INSERT, UPDATE ON message_ownership FROM app_api")
    op.execute(f"GRANT UPDATE ({FLAG_COLUMNS}) ON message_ownership TO app_api")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_user_id() RETURNS bigint
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(pg_catalog.current_setting('app.user_id', true), '')::bigint $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_user_id() RETURNS bigint
        LANGUAGE sql STABLE PARALLEL UNSAFE
        AS $$ SELECT nullif(current_setting('app.user_id', true), '')::bigint $$
        """
    )
    op.execute(f"REVOKE UPDATE ({FLAG_COLUMNS}) ON message_ownership FROM app_api")
    op.execute("GRANT INSERT, UPDATE ON message_ownership TO app_api")
