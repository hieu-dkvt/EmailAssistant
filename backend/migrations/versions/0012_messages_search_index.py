"""Index search từ khóa PGroonga trên subject, body_clean (spec 2026-10-18-keyword-search-design).

Tạo CONCURRENTLY (bảng đã có dữ liệu), nên đặt riêng một migration như 0007. Truy vấn phải viết
đúng biểu thức ARRAY[subject, body_clean] thì planner mới dùng index. Trong phiên app_api có RLS,
planner vẫn dùng index (điều kiện PGroonga là Index Cond, policy là Filter) — đã kiểm 2026-10-18.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-18 10:20:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# unify_* = false: chữ Latin và số cũng cắt bigram (tìm được một phần từ, vd. "prj" trong
# "PRJ-1234"). NFKC150: không phân biệt hoa thường, full-width / half-width.
TOKENIZER = 'TokenNgram("unify_alphabet", false, "unify_symbol", false, "unify_digit", false)'
SEARCH_INDEX = f"""
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_search ON messages
    USING pgroonga ((ARRAY[subject, body_clean]))
    WITH (tokenizer = '{TOKENIZER}', normalizers = 'NormalizerNFKC150')
"""


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(SEARCH_INDEX)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_search")
