"""Build lại index HNSW của message_chunks với m = 16, ef_construction = 128 (spec 27 mục 5).

Tạo index mới tên tạm, xóa index cũ, đổi tên: giữ tên ix_message_chunks_embedding. Tất cả
CONCURRENTLY nên chạy ngoài transaction (autocommit_block, theo mẫu 0007) và không chặn đọc ghi.
IF [NOT] EXISTS: chạy lại sau khi bị ngắt giữa chừng không lỗi. Lỗi giữa chừng có thể để lại
index INVALID tên ix_message_chunks_embedding_new: lần chạy lại xóa nó trước khi tạo index mới.

Downgrade build lại index với tham số mặc định theo cùng mẫu.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-02 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild(with_clause: str) -> None:
    with op.get_context().autocommit_block():
        # Bỏ index _new còn sót (có thể INVALID) của lần chạy trước bị ngắt: IF NOT EXISTS sẽ
        # bỏ qua nó rồi đổi tên một index hỏng vào chỗ index đang dùng.
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_message_chunks_embedding_new")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_message_chunks_embedding_new "
            f"ON message_chunks USING hnsw (embedding halfvec_cosine_ops){with_clause}"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_message_chunks_embedding")
        op.execute(
            "ALTER INDEX IF EXISTS ix_message_chunks_embedding_new "
            "RENAME TO ix_message_chunks_embedding"
        )


def upgrade() -> None:
    _rebuild(" WITH (m = 16, ef_construction = 128)")


def downgrade() -> None:
    _rebuild("")
