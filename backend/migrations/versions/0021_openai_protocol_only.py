"""Chỉ còn giao thức openai cho nhà cung cấp AI (spec 2026-11-01-internal-model-design mục 14).

- Dòng user_ai_provider có giao thức khác đổi sang 'openai', giữ URL và key đã mã hóa.
  Gateway kiểu 9router mở giao thức OpenAI ở đường dẫn /v1: người dùng kiểm tra lại URL gốc ở
  Cài đặt AI (URL cũ thiếu /v1 thì lần gọi trả lỗi ai_bad_request).
- CHECK ck_user_ai_provider_protocol chỉ nhận 'openai'.

Downgrade nhận lại giá trị cũ của CHECK, không khôi phục giao thức của các dòng đã đổi.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-05 09:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE user_ai_provider SET protocol = 'openai' WHERE protocol <> 'openai'")
    op.execute("ALTER TABLE user_ai_provider DROP CONSTRAINT ck_user_ai_provider_protocol")
    op.execute(
        "ALTER TABLE user_ai_provider ADD CONSTRAINT ck_user_ai_provider_protocol "
        "CHECK (protocol = 'openai')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE user_ai_provider DROP CONSTRAINT ck_user_ai_provider_protocol")
    op.execute(
        "ALTER TABLE user_ai_provider ADD CONSTRAINT ck_user_ai_provider_protocol "
        "CHECK (protocol IN ('anthropic', 'openai'))"
    )
