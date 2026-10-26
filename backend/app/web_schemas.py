"""Schema API của web (hợp đồng với web/, sinh kiểu TypeScript qua OpenAPI: npm run gen:api).

Spec: 2026-10-18-web-login-design.md, 2026-10-18-keyword-search-design.md,
2026-10-18-thread-api-design.md. Đổi schema thì sinh lại client của web.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


class ThreadSummary(BaseModel):
    id: int
    subject: str | None
    message_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None
    last_from_name: str | None
    last_from_addr: str | None
    has_attachments: bool


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

type TierName = Literal["haiku", "sonnet", "opus"]


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
    """Đúng nội dung (đã che) gửi Claude trong một lần gọi."""

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
    mask_counts: dict[str, int]
    calls: list[AskCall]


# --- cài đặt model AI (spec 22) ---


class AiTaskInfo(BaseModel):
    task: str
    label: str
    description: str
    tier: TierName
    default_tier: TierName
    model: str | None  # tên model người dùng nhập (chỉ dùng khi có provider tùy chỉnh, spec 24)


class AiTierInfo(BaseModel):
    tier: TierName
    label: str
    model: str
    input_price: float  # USD / 1 triệu token
    output_price: float


class AiSettings(BaseModel):
    configured: bool
    custom_provider: bool  # đang dùng nhà cung cấp tự nhập thay cho ANTHROPIC_API_KEY (spec 24)
    tasks: list[AiTaskInfo]
    tiers: list[AiTierInfo]
    masking: list[str]


class AiSettingsUpdate(StrictModel):
    # Literal viết thẳng (không dùng alias TierName): schema của body nằm trong openapi_extra
    # (json_body), không có $defs để tham chiếu.
    tiers: dict[str, Literal["haiku", "sonnet", "opus"]] = {}
    # Tên model theo tác vụ; null hoặc rỗng = xóa (spec 24 mục 7).
    models: dict[str, str | None] = {}


# --- nhà cung cấp AI tùy chỉnh (spec 24) ---


class AiProviderCustom(BaseModel):
    protocol: Literal["anthropic", "openai"]
    base_url: str
    api_key_set: bool  # false khi key không giải mã được (đổi PMM_SECRET_KEY)
    api_key_hint: str | None  # 4 ký tự cuối, không bao giờ trả key đầy đủ


class AiProvider(BaseModel):
    configured_env: bool  # có ANTHROPIC_API_KEY trong môi trường backend
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
    estimated_usd: float | None


class AiUsage(BaseModel):
    since: datetime
    rows: list[AiUsageRow]
    total_calls: int
    total_estimated_usd: float
