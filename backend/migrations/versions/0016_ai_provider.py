"""Nhà cung cấp AI tùy chỉnh theo người dùng (spec 2026-10-25-ai-provider-design mục 4).

- user_ai_provider: giao thức, URL gốc, API key đã mã hóa (Fernet). RLS theo user_id.
  app_api đọc/ghi/xóa dòng của chính mình. Không chứa mail nhưng chứa bí mật nên cùng quy tắc RLS.
- user_ai_settings.model: tên model người dùng nhập cho tác vụ (dùng khi có provider tùy chỉnh).

Bảng mới, chưa có dữ liệu.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-25 10:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_ai_provider (
            user_id bigint NOT NULL,
            protocol text NOT NULL,
            base_url text NOT NULL,
            api_key_enc text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_ai_provider PRIMARY KEY (user_id),
            CONSTRAINT fk_user_ai_provider_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT ck_user_ai_provider_protocol CHECK (protocol IN ('anthropic', 'openai'))
        )
        """
    )
    op.execute("ALTER TABLE user_ai_provider ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY user_ai_provider_owner ON user_ai_provider "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT, DELETE ON user_ai_provider TO app_api")
    op.execute(
        "GRANT INSERT (user_id, protocol, base_url, api_key_enc) ON user_ai_provider TO app_api"
    )
    op.execute(
        "GRANT UPDATE (protocol, base_url, api_key_enc, updated_at) ON user_ai_provider TO app_api"
    )

    op.execute("ALTER TABLE user_ai_settings ADD COLUMN model text")
    op.execute(
        "ALTER TABLE user_ai_settings ADD CONSTRAINT ck_user_ai_settings_model "
        "CHECK (model IS NULL OR (length(model) BETWEEN 1 AND 200 AND model = btrim(model)))"
    )
    # Dòng chỉ có model (chưa chọn cấp) có tier NULL: đọc ra thì dùng cấp mặc định của tác vụ.
    op.execute("ALTER TABLE user_ai_settings ALTER COLUMN tier DROP NOT NULL")
    op.execute("GRANT INSERT (model) ON user_ai_settings TO app_api")
    op.execute("GRANT UPDATE (model) ON user_ai_settings TO app_api")


def downgrade() -> None:
    op.execute("ALTER TABLE user_ai_settings DROP CONSTRAINT ck_user_ai_settings_model")
    op.execute("ALTER TABLE user_ai_settings DROP COLUMN model")
    op.execute("DELETE FROM user_ai_settings WHERE tier IS NULL")
    op.execute("ALTER TABLE user_ai_settings ALTER COLUMN tier SET NOT NULL")
    op.execute("DROP TABLE user_ai_provider")
