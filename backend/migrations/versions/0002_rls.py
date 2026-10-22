"""Row-Level Security và quyền của role app_api / app_worker.

Người dùng (app_api, app.user_id đặt bằng set_config(..., true)) chỉ thấy mail_sources,
message_ownership của chính mình và messages mà mình có ownership. app_api không ghi
được messages: bước ingest sẽ ghi qua hàm SECURITY DEFINER. app_worker có BYPASSRLS.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17 06:40:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNED_TABLES = ("mail_sources", "message_ownership")
ALL_TABLES = ("users", "mail_sources", "messages", "message_ownership")


def upgrade() -> None:
    # Chưa đặt app.user_id (hoặc chuỗi rỗng) thì trả NULL: mọi policy so sánh với NULL đều sai.
    op.execute(
        """
        CREATE FUNCTION app_user_id() RETURNS bigint
        LANGUAGE sql STABLE
        AS $$ SELECT nullif(current_setting('app.user_id', true), '')::bigint $$
        """
    )

    for table in OWNED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_owner ON {table} "
            "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
        )

    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY messages_owner_select ON messages FOR SELECT
        USING (EXISTS (
            SELECT 1 FROM message_ownership mo
            WHERE mo.message_id = messages.id AND mo.user_id = app_user_id()
        ))
        """
    )

    op.execute("GRANT USAGE ON SCHEMA public TO app_api, app_worker")
    op.execute("GRANT SELECT ON users, messages TO app_api")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(OWNED_TABLES)} TO app_api")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(ALL_TABLES)} TO app_worker")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON {', '.join(ALL_TABLES)} FROM app_api, app_worker")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_api, app_worker")
    op.execute("DROP POLICY messages_owner_select ON messages")
    op.execute("ALTER TABLE messages DISABLE ROW LEVEL SECURITY")
    for table in OWNED_TABLES:
        op.execute(f"DROP POLICY {table}_owner ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_user_id()")
