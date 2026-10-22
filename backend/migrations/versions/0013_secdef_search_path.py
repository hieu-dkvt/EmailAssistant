"""search_path an toàn cho hàm SECURITY DEFINER; PUBLIC không tạo được bảng/kiểu tạm.

Review bảo mật 2026-10-19: 0005 và 0010 đặt `search_path = pg_catalog`. Khi pg_temp không có trong
search_path, PostgreSQL tra pg_temp TRƯỚC pg_catalog với tên bảng và tên kiểu. Một phiên app_api
(vd. qua SQL injection sau này) tạo được `pg_temp.timestamptz` (domain có CHECK gọi hàm của nó),
và `web_session_user` khai báo biến `timestamptz` sẽ chạy CHECK đó bằng quyền app_owner. Sửa:
- `search_path = pg_catalog, pg_temp` cho mọi hàm SECURITY DEFINER (pg_temp ở cuối).
- `REVOKE TEMPORARY ON DATABASE <db> FROM PUBLIC`: app_api, app_worker không cần bảng tạm.

Ghi chú (sửa mô tả của 0010): app_api đọc được bảng users, nên qua `web_password_hash(email)` vẫn
lấy được hash mật khẩu của mọi người dùng, và `web_session_start` tạo được phiên cho mọi user_id.
Hai việc này ngang quyền đặt GUC app.user_id mà API vốn có; các hàm chỉ ngăn đọc thẳng bảng.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-19 09:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SECURITY_DEFINER_FUNCTIONS = (
    "auth_token_user(bytea)",
    "web_password_hash(text)",
    "web_session_start(bigint, bytea, timestamptz)",
    "web_session_user(bytea)",
    "web_session_end(bytea)",
)


def _database_privilege(action: str) -> None:
    op.execute(
        "DO $$ BEGIN EXECUTE format("
        f"'{action} TEMPORARY ON DATABASE %I {'FROM' if action == 'REVOKE' else 'TO'} PUBLIC', "
        "current_database()); END $$"
    )


def upgrade() -> None:
    for signature in SECURITY_DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {signature} SET search_path = pg_catalog, pg_temp")
    _database_privilege("REVOKE")


def downgrade() -> None:
    _database_privilege("GRANT")
    for signature in SECURITY_DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {signature} SET search_path = pg_catalog")
