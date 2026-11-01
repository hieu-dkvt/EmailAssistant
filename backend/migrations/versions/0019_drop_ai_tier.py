"""Bỏ ba cấp model của user_ai_settings (spec 2026-11-01-internal-model-design mục 4).

- Xóa dòng chỉ có cấp (model IS NULL): không còn ý nghĩa khi AI chạy trên model nội bộ.
- DROP COLUMN tier (kèm ck_user_ai_settings_tier và quyền cột của app_api); model NOT NULL.
- GRANT DELETE: xóa tên model của tác vụ là xóa dòng. Policy user_ai_settings_owner không có
  FOR nên áp cho cả DELETE: mỗi người chỉ xóa được dòng của mình.

Downgrade thêm lại cột tier (nullable), không khôi phục dữ liệu đã xóa.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-02 09:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM user_ai_settings WHERE model IS NULL")
    op.execute("ALTER TABLE user_ai_settings DROP COLUMN tier")  # kéo theo ck_..._tier, quyền cột
    op.execute("ALTER TABLE user_ai_settings ALTER COLUMN model SET NOT NULL")
    op.execute("GRANT DELETE ON user_ai_settings TO app_api")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON user_ai_settings FROM app_api")
    op.execute("ALTER TABLE user_ai_settings ALTER COLUMN model DROP NOT NULL")
    op.execute("ALTER TABLE user_ai_settings ADD COLUMN tier text")
    op.execute(
        "ALTER TABLE user_ai_settings ADD CONSTRAINT ck_user_ai_settings_tier "
        "CHECK (tier IN ('haiku', 'sonnet', 'opus'))"
    )
    op.execute("GRANT INSERT (tier) ON user_ai_settings TO app_api")
    op.execute("GRANT UPDATE (tier) ON user_ai_settings TO app_api")
