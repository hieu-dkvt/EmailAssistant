"""Giai đoạn 5: embedding mail, cài đặt model AI, nhật ký gọi Claude.

- message_chunks (spec 2026-10-19-mail-embedding-design mục 4): đoạn của body_clean (chỉ vị trí,
  không chép nội dung) + vector halfvec(1024), index HNSW cosine. FK kép (message_id, user_id)
  ON DELETE CASCADE: xóa mail (kể cả bằng app_api khi xóa mail của folder) xóa luôn vector.
  app_api chỉ đọc; worker ghi.
- user_ai_settings (spec 2026-10-19-ai-settings-design mục 5): cấp model theo tác vụ của từng
  người.
- ai_calls (spec 2026-10-19-ai-ask-design mục 5): mỗi lần gọi Claude — model, token, thời gian.
  Không có nội dung.

Bảng mới, chưa có dữ liệu: index tạo trong transaction (không cần CONCURRENTLY).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-19 14:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AI_CALL_INSERT_COLUMNS = (
    "user_id, task, model, status, input_tokens, output_tokens, cache_read_tokens, "
    "cache_write_tokens, duration_ms"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE message_chunks (
            user_id bigint NOT NULL,
            message_id bigint NOT NULL,
            chunk_no smallint NOT NULL,
            start_pos integer NOT NULL,
            end_pos integer NOT NULL,
            model text NOT NULL,
            chunker_version smallint NOT NULL,
            cleaner_version smallint NOT NULL,
            embedding halfvec(1024) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_message_chunks PRIMARY KEY (user_id, message_id, chunk_no),
            CONSTRAINT fk_message_chunks_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT fk_message_chunks_message_user FOREIGN KEY (message_id, user_id)
                REFERENCES messages (id, user_id) ON DELETE CASCADE,
            CONSTRAINT ck_message_chunks_chunk_no CHECK (chunk_no >= 0),
            CONSTRAINT ck_message_chunks_positions CHECK (0 <= start_pos AND start_pos <= end_pos)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_message_chunks_embedding ON message_chunks "
        "USING hnsw (embedding halfvec_cosine_ops)"
    )
    op.execute("ALTER TABLE message_chunks ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY message_chunks_owner_select ON message_chunks FOR SELECT "
        "USING (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT ON message_chunks TO app_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON message_chunks TO app_worker")

    op.execute(
        """
        CREATE TABLE user_ai_settings (
            user_id bigint NOT NULL,
            task text NOT NULL,
            tier text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_ai_settings PRIMARY KEY (user_id, task),
            CONSTRAINT fk_user_ai_settings_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT ck_user_ai_settings_tier CHECK (tier IN ('haiku', 'sonnet', 'opus'))
        )
        """
    )
    op.execute("ALTER TABLE user_ai_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY user_ai_settings_owner ON user_ai_settings "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT ON user_ai_settings TO app_api")
    op.execute("GRANT INSERT (user_id, task, tier) ON user_ai_settings TO app_api")
    op.execute("GRANT UPDATE (tier, updated_at) ON user_ai_settings TO app_api")

    op.execute(
        """
        CREATE TABLE ai_calls (
            id bigint GENERATED ALWAYS AS IDENTITY,
            user_id bigint NOT NULL,
            task text NOT NULL,
            model text NOT NULL,
            status text NOT NULL,
            input_tokens integer NOT NULL DEFAULT 0,
            output_tokens integer NOT NULL DEFAULT 0,
            cache_read_tokens integer NOT NULL DEFAULT 0,
            cache_write_tokens integer NOT NULL DEFAULT 0,
            duration_ms integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_ai_calls PRIMARY KEY (id),
            CONSTRAINT fk_ai_calls_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT ck_ai_calls_status
                CHECK (status IN ('ok', 'refusal', 'max_tokens', 'error'))
        )
        """
    )
    op.execute("CREATE INDEX ix_ai_calls_user_id_created_at ON ai_calls (user_id, created_at)")
    op.execute("ALTER TABLE ai_calls ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY ai_calls_owner ON ai_calls "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT ON ai_calls TO app_api")
    op.execute(f"GRANT INSERT ({AI_CALL_INSERT_COLUMNS}) ON ai_calls TO app_api")
    op.execute("GRANT SELECT ON ai_calls TO app_worker")


def downgrade() -> None:
    op.execute("DROP TABLE ai_calls")
    op.execute("DROP TABLE user_ai_settings")
    op.execute("DROP TABLE message_chunks")
