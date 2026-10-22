"""Đăng nhập web: mật khẩu và phiên (spec 2026-10-18-web-login-design.md).

- user_passwords, web_sessions: không chứa mail, không RLS, không cấp quyền nào cho app_api /
  app_worker (như api_tokens). API dùng các hàm SECURITY DEFINER dưới đây (search_path cố định,
  chỉ app_api EXECUTE): app_api không đọc được hàng loạt mật khẩu băm hay token phiên.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-18 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTIONS = {
    "web_password_hash(text)": """
        CREATE FUNCTION web_password_hash(p_email text)
        RETURNS TABLE (user_id bigint, password_hash text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT u.id, p.password_hash
            FROM public.users u JOIN public.user_passwords p ON p.user_id = u.id
            WHERE u.email = lower(p_email)
        $$
    """,
    "web_session_start(bigint, bytea, timestamptz)": """
        CREATE FUNCTION web_session_start(
            p_user_id bigint, p_token_hash bytea, p_expires_at timestamptz
        ) RETURNS void
        LANGUAGE sql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            DELETE FROM public.web_sessions
            WHERE user_id = p_user_id AND (expires_at <= now() OR revoked_at IS NOT NULL);
            INSERT INTO public.web_sessions (user_id, token_hash, expires_at)
            VALUES (p_user_id, p_token_hash, p_expires_at);
        $$
    """,
    "web_session_user(bytea)": """
        CREATE FUNCTION web_session_user(p_token_hash bytea) RETURNS bigint
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_id bigint;
            v_user bigint;
            v_seen timestamptz;
        BEGIN
            SELECT id, user_id, last_seen_at INTO v_id, v_user, v_seen
            FROM public.web_sessions
            WHERE token_hash = p_token_hash AND revoked_at IS NULL AND expires_at > now();
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            IF v_seen < now() - interval '5 minutes' THEN
                UPDATE public.web_sessions SET last_seen_at = now() WHERE id = v_id;
            END IF;
            RETURN v_user;
        END
        $$
    """,
    "web_session_end(bytea)": """
        CREATE FUNCTION web_session_end(p_token_hash bytea) RETURNS void
        LANGUAGE sql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            UPDATE public.web_sessions SET revoked_at = now()
            WHERE token_hash = p_token_hash AND revoked_at IS NULL
        $$
    """,
}


def upgrade() -> None:
    op.create_table(
        "user_passwords",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_passwords_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_passwords"),
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_web_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_web_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
    )
    op.create_index("ix_web_sessions_user_id", "web_sessions", ["user_id"])
    op.execute("REVOKE ALL ON user_passwords, web_sessions FROM PUBLIC")
    for signature, body in FUNCTIONS.items():
        op.execute(body)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO app_api")


def downgrade() -> None:
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.drop_index("ix_web_sessions_user_id", table_name="web_sessions")
    op.drop_table("web_sessions")
    op.drop_table("user_passwords")
