"""Gộp/tách thread bằng tay từ web (spec 2026-10-18-thread-api-design.md mục 3).

- thread_overrides: app_api được INSERT theo cột (không có id, created_at) và DELETE, chỉ dòng của
  chính mình (policy). FK kép (message_id, user_id), (target_message_id, user_id) có từ 0006: chỉ
  tham chiếu được mail của chính người đó.
- user_thread_state: API tăng overrides_version mỗi lần thêm/xóa sửa tay; worker ghi
  applied_overrides_version sau khi gom lại. Không chứa nội dung mail.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-18 10:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OVERRIDE_INSERT_COLUMNS = "user_id, action, message_id, target_message_id"


def upgrade() -> None:
    op.create_table(
        "user_thread_state",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("overrides_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("applied_overrides_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_thread_state_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_thread_state"),
    )
    op.execute("ALTER TABLE user_thread_state ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY user_thread_state_owner ON user_thread_state "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT ON user_thread_state TO app_api")
    op.execute("GRANT INSERT (user_id, overrides_version) ON user_thread_state TO app_api")
    op.execute("GRANT UPDATE (overrides_version, updated_at) ON user_thread_state TO app_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_thread_state TO app_worker")

    op.execute(f"GRANT INSERT ({OVERRIDE_INSERT_COLUMNS}) ON thread_overrides TO app_api")
    op.execute("GRANT DELETE ON thread_overrides TO app_api")
    op.execute(
        "CREATE POLICY thread_overrides_owner_insert ON thread_overrides FOR INSERT "
        "WITH CHECK (user_id = app_user_id())"
    )
    op.execute(
        "CREATE POLICY thread_overrides_owner_delete ON thread_overrides FOR DELETE "
        "USING (user_id = app_user_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY thread_overrides_owner_delete ON thread_overrides")
    op.execute("DROP POLICY thread_overrides_owner_insert ON thread_overrides")
    op.execute("REVOKE DELETE ON thread_overrides FROM app_api")
    op.execute(f"REVOKE INSERT ({OVERRIDE_INSERT_COLUMNS}) ON thread_overrides FROM app_api")
    op.execute("REVOKE ALL ON user_thread_state FROM app_api, app_worker")
    op.execute("DROP POLICY user_thread_state_owner ON user_thread_state")
    op.drop_table("user_thread_state")
