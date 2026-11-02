"""Schema API của web (hợp đồng với web/, sinh kiểu TypeScript qua OpenAPI: npm run gen:api).

Spec: 2026-10-18-web-login-design.md, 2026-10-18-keyword-search-design.md,
2026-10-18-thread-api-design.md. Đổi schema thì sinh lại client của web.
"""

import unicodedata
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.mail.domains import normalize_domain
from app.schemas import MAX_BIGINT, StrictModel

type PartKind = Literal["body", "quote", "signature"]


class ErrorBody(BaseModel):
    detail: str
    code: str


class LoginRequest(StrictModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class Segment(BaseModel):
    """Một đoạn văn bản; hit = true là đoạn khớp từ khóa (web hiện bằng <mark>)."""

    text: str
    hit: bool = False


class FolderRef(BaseModel):
    id: int
    path: str


class AddressOut(BaseModel):
    name: str | None = None
    addr: str


class AttachmentOut(BaseModel):
    filename: str | None = None
    size: int
    content_type: str | None = None


class FolderInfo(BaseModel):
    id: int
    folder_path: str
    account: str
    kind: str
    message_count: int
    last_scan_at: datetime | None


# --- search ---


class SearchHit(BaseModel):
    message_id: int
    thread_id: int | None
    thread_message_count: int | None
    subject: list[Segment]
    from_name: str | None
    from_addr: str | None
    sent_at: datetime | None
    has_attachments: bool
    snippet: list[Segment]
    folders: list[FolderRef]
    # Chỉ ở chế độ hybrid (spec 19): khớp nhánh nào.
    match: Literal["keyword", "semantic", "both"] | None = None


class SearchResult(BaseModel):
    total: int  # tối đa 10.000 (hybrid: tối đa 200)
    total_capped: bool  # còn nhiều hơn total (chỉ đếm tới 10.000)
    offset: int
    limit: int
    keywords: list[str]
    items: list[SearchHit]
    mode: Literal["keyword", "hybrid"] = "keyword"
    # Chỉ ở chế độ hybrid: số mail chưa có embedding (đếm tối đa 10.001).
    embedding_pending: int | None = None


# --- thread ---


class ProjectRef(BaseModel):
    id: int
    name: str


class ThreadSummary(BaseModel):
    id: int
    subject: str | None
    message_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None
    last_from_name: str | None
    last_from_addr: str | None
    has_attachments: bool
    projects: list[ProjectRef]  # dự án của các mail trong thread, sắp theo tên


class ThreadPage(BaseModel):
    items: list[ThreadSummary]
    next_cursor: str | None


class ThreadMessage(BaseModel):
    id: int
    thread_parent_id: int | None
    thread_method: str | None
    sent_at: datetime | None
    from_name: str | None
    from_addr: str | None
    to: list[AddressOut]
    cc: list[AddressOut]
    subject: list[Segment]
    body: list[Segment]
    cleaned: bool
    cut_count: int
    body_truncated: bool
    has_attachments: bool
    attachments: list[AttachmentOut]
    folders: list[FolderRef]


class ThreadDetail(BaseModel):
    id: int
    subject: str | None
    message_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None
    keywords: list[str]
    truncated: bool
    messages: list[ThreadMessage]


class MessageRef(BaseModel):
    id: int
    thread_id: int | None


class TextPart(BaseModel):
    kind: PartKind
    segments: list[Segment]


class MessageFull(BaseModel):
    id: int
    keywords: list[str]
    parts: list[TextPart]


# --- sửa tay ---


class OverrideCreate(StrictModel):
    action: Literal["split", "merge"]
    message_id: int = Field(gt=0, le=MAX_BIGINT)
    target_message_id: int | None = Field(default=None, gt=0, le=MAX_BIGINT)

    @model_validator(mode="after")
    def target_matches_action(self) -> "OverrideCreate":
        if (self.action == "merge") != (self.target_message_id is not None):
            raise ValueError("merge cần target_message_id; split không có target_message_id")
        if self.target_message_id == self.message_id:
            raise ValueError("target_message_id phải khác message_id")
        return self


class MessageBrief(BaseModel):
    id: int
    thread_id: int | None
    subject: str | None
    from_name: str | None
    from_addr: str | None
    sent_at: datetime | None


class OverrideInfo(BaseModel):
    id: int
    action: Literal["split", "merge"]
    created_at: datetime
    message: MessageBrief
    target: MessageBrief | None


class ProcessingStatus(BaseModel):
    rethread_pending: bool
    unprocessed_messages: int


# --- thread liên quan (spec 23) ---


class SimilarThread(BaseModel):
    thread_id: int
    subject: str | None
    message_count: int
    last_message_at: datetime | None
    score: float  # độ tương đồng cosine 0–1
    message_id: int  # mail đại diện (đích khi gộp)


class SimilarThreads(BaseModel):
    items: list[SimilarThread]
    pending: bool  # thread chưa có embedding


# --- hỏi đáp AI (spec 21) ---


class AskRequest(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    folder_id: int | None = Field(default=None, gt=0, le=MAX_BIGINT)
    sent_after: datetime | None = None
    sent_before: datetime | None = None


class AskSource(BaseModel):
    n: int
    message_id: int
    thread_id: int | None
    subject: str | None
    from_name: str | None
    from_addr: str | None
    sent_at: datetime | None
    cited: bool


class AskPlan(BaseModel):
    search_queries: list[str]
    keywords: list[str]
    sender: str | None
    sent_after: date | None
    sent_before: date | None
    filters_relaxed: bool  # không có kết quả với bộ lọc của AI: đã tìm lại không dùng bộ lọc đó


class AskSent(BaseModel):
    """Đúng nội dung (text gốc) gửi AI trong một lần gọi."""

    task: str
    content: str


class AskCall(BaseModel):
    task: str
    model: str
    status: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class AskResult(BaseModel):
    answer: str
    insufficient: bool
    sources: list[AskSource]
    plan: AskPlan | None
    sent: list[AskSent]
    calls: list[AskCall]


# --- cài đặt model AI (spec 22) ---


class AiTaskInfo(BaseModel):
    task: str
    label: str
    description: str
    model: str | None  # tên model người dùng nhập; None = chưa nhập (spec 24, 28)


class AiSettings(BaseModel):
    configured: bool  # đã nhập nhà cung cấp AI
    tasks: list[AiTaskInfo]


class AiSettingsUpdate(StrictModel):
    # Tên model theo tác vụ; null hoặc rỗng = xóa (spec 24 mục 7).
    models: dict[str, str | None] = {}


# --- nhà cung cấp AI tùy chỉnh (spec 24) ---


class AiProviderCustom(BaseModel):
    protocol: Literal["anthropic", "openai"]
    base_url: str
    api_key_set: bool  # false khi key không giải mã được (đổi PMM_SECRET_KEY)
    api_key_hint: str | None  # 4 ký tự cuối, không bao giờ trả key đầy đủ
    public_host: bool  # host là IP công cộng (hoặc phân giải ra); web cảnh báo (spec 28)


class AiProvider(BaseModel):
    secret_key_ready: bool  # có PMM_SECRET_KEY hợp lệ (cần để lưu key)
    custom: AiProviderCustom | None


class AiProviderUpdate(StrictModel):
    protocol: Literal["anthropic", "openai"]
    base_url: str
    api_key: str | None = None  # bỏ trống = giữ key đã lưu


class AiProviderTest(BaseModel):
    ok: bool
    model: str
    duration_ms: int


class AiUsageRow(BaseModel):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class AiUsage(BaseModel):
    since: datetime
    rows: list[AiUsageRow]
    total_calls: int


# --- dự án (spec 2026-10-31-project-settings-design mục 6) ---

MAX_PROJECT_NAME = 200
MAX_FILTER_VALUES = 20
MAX_FILTER_VALUE = 255
MAX_URL = 2000
MAX_LOCAL_PATH = 1000
MAX_PROJECT_KEY = 200
URL_SCHEMES = ("http", "https", "ssh")
PROJECT_LIST_FIELDS = (
    "filter_from",
    "filter_to",
    "filter_cc",
    "filter_subject",
    "github_mail",
    "backlog_mail",
)
PROJECT_URL_FIELDS = ("docs_repo_url", "review_repo_url", "backlog_space_url", "redmine_url")
PROJECT_PATH_FIELDS = ("docs_local_path", "review_local_path")
PROJECT_KEY_FIELDS = ("backlog_project_key", "redmine_project")


def _has_control(value: str) -> bool:
    return any(unicodedata.category(c) == "Cc" for c in value)


def _clean_list(values: list[str]) -> list[str]:
    """Bỏ khoảng trắng, từ chối giá trị trống/quá dài/ký tự điều khiển, bỏ trùng sau chuẩn hóa.

    Thông báo lỗi là chuỗi cố định, không chứa giá trị đã gửi.
    """
    from app.projects import normalize  # nhập muộn: app.projects nhập web_schemas

    if len(values) > MAX_FILTER_VALUES:
        raise ValueError(f"Tối đa {MAX_FILTER_VALUES} giá trị")
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = raw.strip()
        # Kiểm tra độ dài trước khi chuẩn hóa NFKC để không xử lý giá trị quá lớn.
        if len(value) > MAX_FILTER_VALUE:
            raise ValueError(f"Mỗi giá trị tối đa {MAX_FILTER_VALUE} ký tự")
        # Giá trị trống (kể cả sau chuẩn hóa) sẽ khớp mọi mail vì "" in text luôn đúng.
        if not value or not normalize(value).strip():
            raise ValueError("Không được để giá trị trống")
        if _has_control(value):
            raise ValueError("Giá trị có ký tự điều khiển")
        key = normalize(value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _check_url(value: str) -> None:
    """Nhận http(s)://, ssh:// và git@host:path. Không gọi tới URL. Lỗi: ValueError cố định."""
    bad = ValueError("URL không hợp lệ")
    if any(c.isspace() for c in value) or _has_control(value):
        raise bad
    if "?" in value or "#" in value:
        raise ValueError("URL không được chứa tham số truy vấn hoặc đoạn #")
    if "://" not in value:
        user, sep, rest = value.partition("@")
        host, colon, path = rest.partition(":")
        if user != "git" or not sep or not colon or not host or not path or "@" in rest:
            raise bad
        if host.startswith("-"):  # git/ssh hiểu nhầm là tùy chọn
            raise bad
        return
    try:
        parts = urlsplit(value)
        host = parts.hostname
        password = parts.password
        userinfo = "@" in parts.netloc
    except ValueError:
        raise bad from None
    if parts.scheme not in URL_SCHEMES or not host or host.startswith("-"):
        raise bad
    if parts.scheme == "ssh":
        if password is not None:  # ssh://git@host/… không có mật khẩu thì được
            raise ValueError("URL không được chứa mật khẩu")
        if parts.username not in (None, "git"):  # token có thể nằm ở vị trí tên đăng nhập
            raise ValueError("URL ssh chỉ nhận tên đăng nhập git")
    elif userinfo:
        raise ValueError("URL không được chứa tên đăng nhập hoặc mật khẩu")


def _optional_text(value: str | None, max_len: int, *, url: bool = False) -> str | None:
    """Bỏ khoảng trắng; trống → None."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > max_len:
        raise ValueError(f"Tối đa {max_len} ký tự")
    if url:
        _check_url(value)
    elif _has_control(value):
        raise ValueError("Có ký tự điều khiển")
    return value


class ProjectInput(StrictModel):
    """Body của POST/PUT /projects. Validator theo từng trường để web hiện lỗi đúng ô (loc)."""

    name: str
    filter_from: list[str] = []
    filter_to: list[str] = []
    filter_cc: list[str] = []
    filter_subject: list[str] = []
    docs_repo_url: str | None = None
    docs_local_path: str | None = None
    github_mail: list[str] = []
    review_repo_url: str | None = None
    review_local_path: str | None = None
    backlog_mail: list[str] = []
    backlog_space_url: str | None = None
    backlog_project_key: str | None = None
    redmine_url: str | None = None
    redmine_project: str | None = None
    customer_domains: list[str] = []
    processed_source_id: int | None = Field(default=None, gt=0, le=MAX_BIGINT)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_PROJECT_NAME:
            raise ValueError(f"Tên dự án dài 1–{MAX_PROJECT_NAME} ký tự")
        if _has_control(value):
            raise ValueError("Tên dự án có ký tự điều khiển")
        return value

    @field_validator(*PROJECT_LIST_FIELDS)
    @classmethod
    def _list(cls, value: list[str]) -> list[str]:
        return _clean_list(value)

    @field_validator("customer_domains")
    @classmethod
    def _domains(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_FILTER_VALUES:
            raise ValueError(f"Tối đa {MAX_FILTER_VALUES} domain")
        out: list[str] = []
        for raw in value:
            domain = normalize_domain(raw)
            if domain not in out:
                out.append(domain)
        return out

    @field_validator(*PROJECT_URL_FIELDS)
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _optional_text(value, MAX_URL, url=True)

    @field_validator(*PROJECT_PATH_FIELDS)
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        return _optional_text(value, MAX_LOCAL_PATH)

    @field_validator(*PROJECT_KEY_FIELDS)
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        return _optional_text(value, MAX_PROJECT_KEY)


class Project(BaseModel):
    id: int
    name: str
    filter_from: list[str]
    filter_to: list[str]
    filter_cc: list[str]
    filter_subject: list[str]
    docs_repo_url: str | None
    docs_local_path: str | None
    github_mail: list[str]
    review_repo_url: str | None
    review_local_path: str | None
    backlog_mail: list[str]
    backlog_space_url: str | None
    backlog_project_key: str | None
    redmine_url: str | None
    redmine_project: str | None
    customer_domains: list[str]
    processed_source_id: int | None
    ai_since: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseModel):
    items: list[Project]
    assigning: bool  # rules_version > applied_rules_version: worker đang gán lại mail


# --- Glossary (spec 26 mục 9) ---

MAX_GLOSSARY_TERM = 200
MIN_GLOSSARY_TERM = 2
MAX_GLOSSARY_TRANSLATION = 200
MAX_GLOSSARY_NOTE = 500


class GlossaryInput(StrictModel):
    """Body của POST/PATCH /glossary. PATCH không đổi phạm vi: bỏ qua project_id."""

    project_id: int | None = Field(default=None, gt=0, le=MAX_BIGINT)
    term: str
    translation: str
    note: str | None = None

    @field_validator("term")
    @classmethod
    def _term(cls, value: str) -> str:
        value = value.strip()
        if not MIN_GLOSSARY_TERM <= len(value) <= MAX_GLOSSARY_TERM:
            raise ValueError(f"Thuật ngữ dài {MIN_GLOSSARY_TERM}–{MAX_GLOSSARY_TERM} ký tự")
        if _has_control(value):
            raise ValueError("Có ký tự điều khiển")
        return value

    @field_validator("translation")
    @classmethod
    def _translation(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= MAX_GLOSSARY_TRANSLATION:
            raise ValueError(f"Bản dịch dài 1–{MAX_GLOSSARY_TRANSLATION} ký tự")
        if _has_control(value):
            raise ValueError("Có ký tự điều khiển")
        return value

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > MAX_GLOSSARY_NOTE:
            raise ValueError(f"Ghi chú tối đa {MAX_GLOSSARY_NOTE} ký tự")
        if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
            raise ValueError("Có ký tự điều khiển")
        return value or None


class GlossaryItem(BaseModel):
    id: int
    project_id: int | None
    term: str
    translation: str
    note: str | None
    created_at: datetime
    updated_at: datetime


class GlossaryPage(BaseModel):
    items: list[GlossaryItem]
    total: int


class GlossaryImportInput(StrictModel):
    project_id: int | None = Field(default=None, gt=0, le=MAX_BIGINT)
    text: str
    format: Literal["csv", "lines"]
    on_conflict: Literal["skip", "overwrite"] = "skip"
    apply: bool = False


class GlossaryImportResult(BaseModel):
    added: int
    duplicate: int
    invalid: int
    first_error: str | None  # chỉ có số dòng và tên ô, không chép giá trị


# --- Dịch mail (spec 26 mục 6, 10) ---


class TranslateInput(StrictModel):
    force: bool = False  # true: gọi AI lại dù đã có bản dịch ("Dịch lại")


class MoveInput(StrictModel):
    project_id: int = Field(gt=0, le=MAX_BIGINT)


class MailMoveOut(BaseModel):
    """Trạng thái chuyển folder của mail sau thao tác (spec 26 mục 8)."""

    message_id: int
    folder_id: int | None  # folder đích; None: mail ở chỗ cũ
    move_note: str | None  # no_folder, ambiguous


class MessageTranslationOut(BaseModel):
    message_id: int
    translated_subject: str
    translated_body: str
    notes: list[str]
    warnings: list[str]  # mã kiểm tra sau dịch, spec 26 mục 11
    model: str
    stale: bool  # mail, prompt hoặc thuật ngữ đã dùng đã đổi: nên dịch lại
    created_at: datetime
    updated_at: datetime


class AutoState(BaseModel):
    """Trạng thái xử lý tự động của một mail (spec 26 mục 8)."""

    status: Literal["retry", "done", "failed"]
    error_code: str | None
    move_note: str | None
    target_folder: str | None  # đường dẫn folder đã chuyển tới, None = mail ở chỗ cũ


class ThreadTranslationItem(BaseModel):
    message_id: int
    translation: MessageTranslationOut | None
    auto: AutoState | None
    projects: list[ProjectRef]  # dự án của mail (message_projects), sắp theo tên
    move_projects: list[ProjectRef]  # phần có folder đích: chọn được khi chuyển mail


class ThreadTranslations(BaseModel):
    items: list[ThreadTranslationItem]
