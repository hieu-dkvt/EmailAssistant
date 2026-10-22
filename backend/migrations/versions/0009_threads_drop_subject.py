"""Bỏ threads.subject, threads.subject_norm (review bảo mật 2026-10-17).

Đó là bản sao subject của mail đầu thread. Xóa mail của folder (app_api) xóa messages nhưng
app_api không ghi được threads, nên subject còn lại trên server tới khi worker gom lại. Từ đây
threads không chứa nội dung mail (chỉ số đếm, ngày); subject lấy từ mail root của thread khi đọc.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-18 00:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("threads", "subject_norm")
    op.drop_column("threads", "subject")


def downgrade() -> None:
    # Giá trị cũ không khôi phục được; chạy process-mail --rethread sau khi downgrade.
    op.add_column("threads", sa.Column("subject", sa.Text(), nullable=True))
    op.add_column(
        "threads",
        sa.Column("subject_norm", sa.Text(), server_default=sa.text("''"), nullable=False),
    )
