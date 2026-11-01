"""Dịch tự động, glossary, chuyển folder (spec 2026-10-31-mail-translation-design mục 3, 8-10).

- projects: thêm customer_domains, processed_source_id, ai_since (app_api ghi).
- glossary_terms: thuật ngữ chung (project_id NULL) hoặc của dự án. app_api ghi.
- message_translations: bản dịch đã lưu. Ngoại lệ có chủ đích: chứa chữ dịch, nhưng do app_api
  ghi và xóa lan truyền theo mail.
- message_auto_runs: trạng thái xử lý tự động từng mail, folder đã chuyển tới. app_api ghi.
app_worker chỉ đọc message_auto_runs (hàm liệt kê người dùng cần quét, không đọc nội dung).

Bảng mới, chưa có dữ liệu.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-31 14:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECT_NEW_COLS = "customer_domains, processed_source_id, ai_since"
GLOSSARY_COLS = "project_id, term, term_norm, translation, note, created_at, updated_at"
TRANSLATION_COLS = (
    "translated_subject, translated_body, notes, warnings, source_hash, model, prompt_version, "
    "glossary_used, created_at, updated_at"
)
AUTO_RUN_COLS = (
    "status, attempts, next_attempt_at, error_code, from_source_id, target_source_id, "
    "move_note, created_at, updated_at"
)


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE projects
            ADD COLUMN customer_domains text[] NOT NULL DEFAULT '{}',
            ADD COLUMN processed_source_id bigint,
            ADD COLUMN ai_since timestamptz,
            ADD CONSTRAINT fk_projects_processed_source_mail_sources
                FOREIGN KEY (processed_source_id, user_id)
                REFERENCES mail_sources (id, user_id) ON DELETE SET NULL (processed_source_id)
        """
    )
    op.execute(f"GRANT INSERT ({PROJECT_NEW_COLS}) ON projects TO app_api")
    op.execute(f"GRANT UPDATE ({PROJECT_NEW_COLS}) ON projects TO app_api")

    op.execute(
        """
        CREATE TABLE glossary_terms (
            id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
            user_id bigint NOT NULL,
            project_id bigint,
            term text NOT NULL,
            term_norm text NOT NULL,
            translation text NOT NULL,
            note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_glossary_terms PRIMARY KEY (id),
            CONSTRAINT fk_glossary_terms_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT fk_glossary_terms_project_user FOREIGN KEY (project_id, user_id)
                REFERENCES projects (id, user_id) ON DELETE CASCADE,
            CONSTRAINT uq_glossary_terms_scope
                UNIQUE NULLS NOT DISTINCT (user_id, project_id, term_norm)
        )
        """
    )
    op.execute("ALTER TABLE glossary_terms ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY glossary_terms_owner ON glossary_terms "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT, DELETE ON glossary_terms TO app_api")
    op.execute(f"GRANT INSERT (user_id, {GLOSSARY_COLS}) ON glossary_terms TO app_api")
    op.execute(f"GRANT UPDATE ({GLOSSARY_COLS}) ON glossary_terms TO app_api")

    op.execute(
        """
        CREATE TABLE message_translations (
            user_id bigint NOT NULL,
            message_id bigint NOT NULL,
            translated_subject text NOT NULL,
            translated_body text NOT NULL,
            notes jsonb NOT NULL DEFAULT '[]',
            warnings jsonb NOT NULL DEFAULT '[]',
            source_hash bytea NOT NULL,
            model text NOT NULL,
            prompt_version integer NOT NULL,
            glossary_used jsonb NOT NULL DEFAULT '[]',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_message_translations PRIMARY KEY (user_id, message_id),
            CONSTRAINT fk_message_translations_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT fk_message_translations_message_user FOREIGN KEY (message_id, user_id)
                REFERENCES messages (id, user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("ALTER TABLE message_translations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY message_translations_owner ON message_translations "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT, DELETE ON message_translations TO app_api")
    op.execute(
        f"GRANT INSERT (user_id, message_id, {TRANSLATION_COLS}) ON message_translations TO app_api"
    )
    op.execute(f"GRANT UPDATE ({TRANSLATION_COLS}) ON message_translations TO app_api")

    op.execute(
        """
        CREATE TABLE message_auto_runs (
            user_id bigint NOT NULL,
            message_id bigint NOT NULL,
            status text NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz,
            error_code text,
            from_source_id bigint,
            target_source_id bigint,
            move_note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_message_auto_runs PRIMARY KEY (user_id, message_id),
            CONSTRAINT ck_message_auto_runs_status CHECK (status IN ('retry', 'done', 'failed')),
            CONSTRAINT fk_message_auto_runs_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT fk_message_auto_runs_message_user FOREIGN KEY (message_id, user_id)
                REFERENCES messages (id, user_id) ON DELETE CASCADE,
            CONSTRAINT fk_message_auto_runs_from_source_user FOREIGN KEY (from_source_id, user_id)
                REFERENCES mail_sources (id, user_id) ON DELETE SET NULL (from_source_id),
            CONSTRAINT fk_message_auto_runs_target_source_user
                FOREIGN KEY (target_source_id, user_id)
                REFERENCES mail_sources (id, user_id) ON DELETE SET NULL (target_source_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_message_auto_runs_user_id_target_source_id ON message_auto_runs "
        "(user_id, target_source_id)"
    )
    op.execute("ALTER TABLE message_auto_runs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY message_auto_runs_owner ON message_auto_runs "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT, DELETE ON message_auto_runs TO app_api")
    op.execute(
        f"GRANT INSERT (user_id, message_id, {AUTO_RUN_COLS}) ON message_auto_runs TO app_api"
    )
    op.execute(f"GRANT UPDATE ({AUTO_RUN_COLS}) ON message_auto_runs TO app_api")
    op.execute("GRANT SELECT ON message_auto_runs TO app_worker")


def downgrade() -> None:
    op.execute("DROP TABLE message_auto_runs")
    op.execute("DROP TABLE message_translations")
    op.execute("DROP TABLE glossary_terms")
    op.execute(
        "ALTER TABLE projects DROP CONSTRAINT fk_projects_processed_source_mail_sources, "
        "DROP COLUMN ai_since, DROP COLUMN processed_source_id, DROP COLUMN customer_domains"
    )
