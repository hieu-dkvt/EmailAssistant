"""Model SQLAlchemy của các bảng mail.

Schema chi tiết và lý do từng cột: docs/superpowers/specs/2026-10-16-db-schema-design.md (mục 7.3),
2026-10-17-content-cleaning-design.md (mục 3), 2026-10-17-threading-design.md (mục 3).
RLS, policy và GRANT nằm trong migration (SQL viết tay), không thể hiện ở đây.
"""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}

EMPTY_JSON_ARRAY = text("'[]'::jsonb")
FALSE = text("false")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'pm', 'member')", name="role"),
        CheckConstraint("email = lower(email)", name="email_lower"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default=text("'member'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiToken(Base):
    """Token của collector. Chỉ lưu SHA-256; app_api không đọc được bảng này
    (xác thực qua hàm SECURITY DEFINER auth_token_user, migration 0005)."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    label: Mapped[str] = mapped_column(Text, server_default=text("'collector'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserPassword(Base):
    """Mật khẩu web (scrypt). app_api không đọc được bảng này (hàm web_password_hash, 0010)."""

    __tablename__ = "user_passwords"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebSession(Base):
    """Phiên đăng nhập web. Chỉ lưu SHA-256 của token; app_api dùng hàm web_session_* (0010)."""

    __tablename__ = "web_sessions"
    __table_args__ = (Index("ix_web_sessions_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailSource(Base):
    """Một folder mbox của một người dùng (đường dẫn tương đối trong profile, như config.json)."""

    __tablename__ = "mail_sources"
    __table_args__ = (
        CheckConstraint("kind IN ('local', 'imap_cache')", name="kind"),
        UniqueConstraint("user_id", "folder_path"),
        # Cho FK kép từ message_ownership: folder phải thuộc đúng người dùng.
        UniqueConstraint("id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    account: Mapped[str] = mapped_column(Text)
    folder_path: Mapped[str] = mapped_column(Text)
    # Collector báo lên, chỉ để hiển thị; trạng thái quét thật nằm trong SQLite của collector.
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    last_offset: Mapped[int | None] = mapped_column(BigInteger)
    last_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    """Bản mail của một người dùng.

    Mỗi người có bản riêng, không dùng chung giữa người dùng (quyết định 2026-10-17): dùng chung
    theo Message-ID cho phép ai biết Message-ID (vd. từ header References) chiếm được mail.
    Trong một người dùng, cùng mail ở nhiều folder vẫn là một bản (message_ownership).
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id_hash"),
        # Cho FK kép từ message_ownership: ownership chỉ trỏ tới mail của đúng người dùng.
        UniqueConstraint("id", "user_id"),
        # Mọi truy vấn đều lọc theo user (RLS) nên index bắt đầu bằng user_id.
        Index("ix_messages_user_id_sent_at", "user_id", "sent_at"),
        Index("ix_messages_user_id_from_addr", "user_id", "from_addr"),
        Index("ix_messages_user_id_thread_id", "user_id", "thread_id"),
        Index("ix_messages_thread_parent_id", "thread_parent_id"),
        # Search từ khóa PGroonga (migration 0012, tokenizer/normalizer ghi ở đó).
        Index(
            "ix_messages_search",
            text("(ARRAY[subject, body_clean])"),
            postgresql_using="pgroonga",
        ),
        CheckConstraint(
            "thread_method IN ('root', 'references', 'thread_index', 'subject', 'merge')",
            name="thread_method",
        ),
        # Mail chỉ nằm trong thread / trả lời mail của chính người dùng. SET NULL theo cột:
        # xóa thread hoặc mail cha không đụng tới user_id của mail.
        ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["threads.id", "threads.user_id"],
            name="fk_messages_thread_user",
            ondelete="SET NULL (thread_id)",
        ),
        ForeignKeyConstraint(
            ["thread_parent_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_messages_thread_parent_user",
            ondelete="SET NULL (thread_parent_id)",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    message_id_hash: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    from_addr: Mapped[str | None] = mapped_column(Text)
    from_name: Mapped[str | None] = mapped_column(Text)
    to_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=EMPTY_JSON_ARRAY)
    cc_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=EMPTY_JSON_ARRAY)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_tz_offset_min: Mapped[int | None] = mapped_column(SmallInteger)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    thread_index: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, server_default=text("''"))
    body_from_html: Mapped[bool] = mapped_column(Boolean, server_default=FALSE)
    has_attachments: Mapped[bool] = mapped_column(Boolean, server_default=FALSE)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=EMPTY_JSON_ARRAY
    )
    parser_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Làm sạch (worker ghi): null = chưa làm sạch.
    body_clean: Mapped[str | None] = mapped_column(Text)
    clean_cuts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    cleaner_version: Mapped[int | None] = mapped_column(SmallInteger)
    # Gom thread (worker ghi): thread_id null = chưa gom.
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    thread_parent_id: Mapped[int | None] = mapped_column(BigInteger)
    thread_method: Mapped[str | None] = mapped_column(Text)


class Thread(Base):
    """Thread của một người dùng. Gom lại toàn bộ mỗi lần chạy; id giữ theo số mail chung.

    Không chứa nội dung mail (subject lấy từ mail root khi đọc): xóa mail thì không còn bản sao.
    """

    __tablename__ = "threads"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        # Đúng thứ tự của danh sách thread trên web (migration 0014).
        Index(
            "ix_threads_list",
            "user_id",
            text("last_message_at DESC NULLS LAST"),
            text("id DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    message_count: Mapped[int] = mapped_column(Integer)
    first_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    threader_version: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadOverride(Base):
    """Gộp/tách thread bằng tay. Tham chiếu mail vì id thread có thể đổi khi gom lại."""

    __tablename__ = "thread_overrides"
    __table_args__ = (
        CheckConstraint("action IN ('split', 'merge')", name="action"),
        CheckConstraint("(action = 'merge') = (target_message_id IS NOT NULL)", name="target"),
        CheckConstraint("target_message_id <> message_id", name="not_self"),
        ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_thread_overrides_message_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_thread_overrides_target_user",
            ondelete="CASCADE",
        ),
        Index("ix_thread_overrides_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(Text)
    message_id: Mapped[int] = mapped_column(BigInteger)
    target_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserThreadState(Base):
    """Sửa tay chưa áp dụng: API tăng overrides_version, worker ghi applied_overrides_version
    sau khi gom lại (spec 2026-10-18-thread-api-design.md mục 3, 6)."""

    __tablename__ = "user_thread_state"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    overrides_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    applied_overrides_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    """Cấu hình dự án của một người (spec 2026-10-31-project-settings-design mục 4)."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("user_id", "name"), UniqueConstraint("id", "user_id"))

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    filter_from: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    filter_to: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    filter_cc: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    filter_subject: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    docs_repo_url: Mapped[str | None] = mapped_column(Text)
    docs_local_path: Mapped[str | None] = mapped_column(Text)
    github_mail: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    review_repo_url: Mapped[str | None] = mapped_column(Text)
    review_local_path: Mapped[str | None] = mapped_column(Text)
    backlog_mail: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    backlog_space_url: Mapped[str | None] = mapped_column(Text)
    backlog_project_key: Mapped[str | None] = mapped_column(Text)
    redmine_url: Mapped[str | None] = mapped_column(Text)
    redmine_project: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageProject(Base):
    """Mail thuộc dự án nào; chỉ worker ghi. Không chép nội dung mail."""

    __tablename__ = "message_projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_message_projects_message_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["projects.id", "projects.user_id"],
            name="fk_message_projects_project_user",
            ondelete="CASCADE",
        ),
        Index("ix_message_projects_user_id_project_id", "user_id", "project_id"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class UserProjectState(Base):
    """Bộ lọc đổi chưa áp dụng và tiến độ quét lại (spec 25 mục 4, 5)."""

    __tablename__ = "user_project_state"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    rules_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    applied_rules_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    scan_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    scan_after_id: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageChunk(Base):
    """Một đoạn body_clean đã embed (spec 2026-10-19-mail-embedding-design mục 4).

    Chỉ lưu vị trí [start_pos, end_pos) trên body_clean, không chép nội dung. app_api chỉ đọc.
    """

    __tablename__ = "message_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_message_chunks_message_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("chunk_no >= 0", name="chunk_no"),
        CheckConstraint("0 <= start_pos AND start_pos <= end_pos", name="positions"),
        Index(
            "ix_message_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chunk_no: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    start_pos: Mapped[int] = mapped_column(Integer)
    end_pos: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(Text)
    chunker_version: Mapped[int] = mapped_column(SmallInteger)
    cleaner_version: Mapped[int] = mapped_column(SmallInteger)
    embedding: Mapped[Any] = mapped_column(HALFVEC(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAiSetting(Base):
    """Cấp model AI theo tác vụ của từng người (spec 2026-10-19-ai-settings-design mục 5)."""

    __tablename__ = "user_ai_settings"
    __table_args__ = (CheckConstraint("tier IN ('haiku', 'sonnet', 'opus')", name="tier"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    task: Mapped[str] = mapped_column(Text, primary_key=True)
    tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tên model người dùng nhập (spec 2026-10-25-ai-provider-design mục 4); NULL = dùng cấp.
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAiProvider(Base):
    """Nhà cung cấp AI tùy chỉnh của từng người; API key đã mã hóa (spec 24 mục 4, 5)."""

    __tablename__ = "user_ai_provider"
    __table_args__ = (CheckConstraint("protocol IN ('anthropic', 'openai')", name="protocol"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    protocol: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    api_key_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiCall(Base):
    """Một lần gọi Claude: model, token, thời gian. Không có nội dung (spec 21 mục 5)."""

    __tablename__ = "ai_calls"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'refusal', 'max_tokens', 'error')", name="status"),
        Index("ix_ai_calls_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    task: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_write_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duration_ms: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageOwnership(Base):
    """Người dùng nào có mail nào, ở folder nào, vị trí nào trong mbox."""

    __tablename__ = "message_ownership"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["mail_sources.id", "mail_sources.user_id"],
            name="fk_message_ownership_source_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_message_ownership_message_user",
            ondelete="CASCADE",
        ),
        Index("ix_message_ownership_user_id_message_id", "user_id", "message_id"),
    )

    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    mbox_offset: Mapped[int] = mapped_column(BigInteger)
    is_read: Mapped[bool] = mapped_column(Boolean, server_default=FALSE)
    is_replied: Mapped[bool] = mapped_column(Boolean, server_default=FALSE)
    is_flagged: Mapped[bool] = mapped_column(Boolean, server_default=FALSE)
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default=FALSE)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
