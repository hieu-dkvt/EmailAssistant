"""Hàng đợi job Procrastinate trong schema riêng `procrastinate` (spec 2026-10-17-worker-design.md).

- Schema SQL lấy từ bản Procrastinate đã ghim trong pyproject (3.10.0). Nâng bản Procrastinate thì
  thêm migration chạy các file trong procrastinate/sql/migrations, không sửa migration này.
- Chỉ app_worker có USAGE trên schema: app_api (chịu RLS, phục vụ người dùng) không thấy bảng job.
- Kết nối của worker đặt search_path=procrastinate (app/tasks).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17 17:00:00
"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "procrastinate"


def upgrade() -> None:
    bind = op.get_bind()
    old_path = bind.exec_driver_sql("SELECT current_setting('search_path')").scalar_one()
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    bind.exec_driver_sql(f"SET LOCAL search_path TO {SCHEMA}")
    # Gửi thẳng cho psycopg (không qua text(): SQL có ':'). SQLAlchemy vẫn truyền bộ tham số rỗng
    # nên psycopg đọc '%' là placeholder: nhân đôi như SchemaManager.apply_schema. Không có tham
    # số thật nên psycopg dùng simple protocol, chạy được nhiều câu lệnh.
    bind.exec_driver_sql(SchemaManager.get_schema().replace("%", "%%"))
    bind.exec_driver_sql("SELECT set_config('search_path', %s, true)", (old_path,))

    op.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO app_worker")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {SCHEMA} TO app_worker"
    )
    op.execute(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO app_worker")
    op.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {SCHEMA} TO app_worker")


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
