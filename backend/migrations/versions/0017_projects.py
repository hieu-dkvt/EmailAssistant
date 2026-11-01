"""Dự án và liên kết mail-dự án (spec 2026-10-31-project-settings-design mục 4).

- projects: cấu hình dự án của từng người (tên, bộ lọc 4 ô, các liên kết lưu sẵn). RLS theo user_id.
  app_api ghi (không được chọn/đổi id, user_id); app_worker chỉ đọc.
- message_projects: mail thuộc dự án nào; chỉ worker ghi. FK kép cùng user_id, xóa lan truyền.
- user_project_state: API tăng rules_version, worker ghi tiến độ quét (mẫu user_thread_state).

Bảng mới, chưa có dữ liệu.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-31 10:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECT_COLS = (
    "name, filter_from, filter_to, filter_cc, filter_subject, docs_repo_url, docs_local_path, "
    "github_mail, review_repo_url, review_local_path, backlog_mail, backlog_space_url, "
    "backlog_project_key, redmine_url, redmine_project, created_at, updated_at"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE projects (
            id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
            user_id bigint NOT NULL,
            name text NOT NULL,
            filter_from text[] NOT NULL DEFAULT '{}',
            filter_to text[] NOT NULL DEFAULT '{}',
            filter_cc text[] NOT NULL DEFAULT '{}',
            filter_subject text[] NOT NULL DEFAULT '{}',
            docs_repo_url text,
            docs_local_path text,
            github_mail text[] NOT NULL DEFAULT '{}',
            review_repo_url text,
            review_local_path text,
            backlog_mail text[] NOT NULL DEFAULT '{}',
            backlog_space_url text,
            backlog_project_key text,
            redmine_url text,
            redmine_project text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_projects PRIMARY KEY (id),
            CONSTRAINT fk_projects_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT uq_projects_user_id_name UNIQUE (user_id, name),
            CONSTRAINT uq_projects_id_user_id UNIQUE (id, user_id)
        )
        """
    )
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY projects_owner ON projects "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT, DELETE ON projects TO app_api")
    # Không cấp cột id, user_id cho UPDATE; INSERT có user_id nhưng không có id.
    op.execute(f"GRANT INSERT (user_id, {PROJECT_COLS}) ON projects TO app_api")
    op.execute(f"GRANT UPDATE ({PROJECT_COLS}) ON projects TO app_api")
    op.execute("GRANT SELECT ON projects TO app_worker")

    op.execute(
        """
        CREATE TABLE message_projects (
            user_id bigint NOT NULL,
            message_id bigint NOT NULL,
            project_id bigint NOT NULL,
            CONSTRAINT pk_message_projects PRIMARY KEY (user_id, message_id, project_id),
            CONSTRAINT fk_message_projects_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT fk_message_projects_message_user FOREIGN KEY (message_id, user_id)
                REFERENCES messages (id, user_id) ON DELETE CASCADE,
            CONSTRAINT fk_message_projects_project_user FOREIGN KEY (project_id, user_id)
                REFERENCES projects (id, user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_message_projects_user_id_project_id ON message_projects "
        "(user_id, project_id)"
    )
    op.execute("ALTER TABLE message_projects ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY message_projects_owner ON message_projects "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT ON message_projects TO app_api")
    op.execute("GRANT SELECT, INSERT, DELETE ON message_projects TO app_worker")

    op.execute(
        """
        CREATE TABLE user_project_state (
            user_id bigint NOT NULL,
            rules_version bigint NOT NULL DEFAULT 0,
            applied_rules_version bigint NOT NULL DEFAULT 0,
            scan_version bigint NOT NULL DEFAULT 0,
            scan_after_id bigint NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_project_state PRIMARY KEY (user_id),
            CONSTRAINT fk_user_project_state_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("ALTER TABLE user_project_state ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY user_project_state_owner ON user_project_state "
        "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
    )
    op.execute("GRANT SELECT ON user_project_state TO app_api")
    op.execute("GRANT INSERT (user_id, rules_version) ON user_project_state TO app_api")
    op.execute("GRANT UPDATE (rules_version, updated_at) ON user_project_state TO app_api")
    op.execute("GRANT SELECT ON user_project_state TO app_worker")
    op.execute(
        "GRANT UPDATE (applied_rules_version, scan_version, scan_after_id, updated_at) "
        "ON user_project_state TO app_worker"
    )


def downgrade() -> None:
    op.execute("DROP TABLE user_project_state")
    op.execute("DROP TABLE message_projects")
    op.execute("DROP TABLE projects")
