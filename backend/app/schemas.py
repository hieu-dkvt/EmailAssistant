"""Schema của Ingest API (hợp đồng với collector). Spec: 2026-10-17-ingest-api-design.md."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGES_PER_BATCH = 500
MAX_BIGINT = 2**63 - 1
MIN_YEAR, MAX_YEAR = 1970, 2100


def _plausible_time(value: datetime | None) -> datetime | None:
    """Ngày ngoài 1970–2100 (hoặc không đổi được sang UTC) coi như không có.

    Header Date sai không được làm hỏng cả lô (spec ingest mục 2).
    """
    if value is None:
        return None
    try:
        utc = value.astimezone(UTC) if value.tzinfo else value
    except OverflowError:
        return None
    if not (MIN_YEAR <= value.year <= MAX_YEAR and MIN_YEAR <= utc.year <= MAX_YEAR):
        return None
    return value


PlausibleTime = Annotated[datetime | None, AfterValidator(_plausible_time)]
BigInt = Annotated[int, Field(ge=0, le=MAX_BIGINT)]


def _strip_nul(value: Any) -> Any:
    """PostgreSQL không nhận ký tự NUL trong text/jsonb."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [v.replace("\x00", "") if isinstance(v, str) else v for v in value]
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("*", mode="before")
    @classmethod
    def strip_nul(cls, value: Any) -> Any:
        return _strip_nul(value)


class Address(StrictModel):
    name: str | None = Field(default=None, max_length=1000)
    addr: str = Field(max_length=1000)


class Attachment(StrictModel):
    filename: str | None = Field(default=None, max_length=1000)
    size: BigInt
    content_type: str | None = Field(default=None, max_length=255)


class Flags(StrictModel):
    read: bool = False
    replied: bool = False
    flagged: bool = False


class IngestMessage(StrictModel):
    message_id: str | None = Field(default=None, max_length=998)
    subject: str | None = Field(default=None, max_length=10_000)
    from_: Address | None = Field(default=None, alias="from")
    to: list[Address] = Field(default_factory=list, max_length=1000)
    cc: list[Address] = Field(default_factory=list, max_length=1000)
    date: PlausibleTime = None
    date_tz_offset_min: int | None = Field(default=None, ge=-840, le=840)
    in_reply_to: str | None = Field(default=None, max_length=998)
    references: list[Annotated[str, Field(max_length=998)]] = Field(
        default_factory=list, max_length=500
    )
    thread_index: str | None = Field(default=None, max_length=1000)
    body_text: str = Field(default="", max_length=2_000_000)
    body_from_html: bool = False
    attachments: list[Attachment] = Field(default_factory=list, max_length=200)
    mbox_offset: BigInt
    flags: Flags = Field(default_factory=Flags)


class IngestSource(StrictModel):
    folder_path: str = Field(min_length=1, max_length=1024)
    account: str = Field(max_length=255)
    kind: Literal["local", "imap_cache"]
    file_size: BigInt | None = None
    last_offset: BigInt | None = None
    last_mtime: PlausibleTime = None


class IngestBatch(StrictModel):
    schema_version: Literal[1]
    parser_version: str = Field(min_length=1, max_length=64)
    source: IngestSource
    messages: list[IngestMessage] = Field(max_length=MAX_MESSAGES_PER_BATCH)


class IngestResult(BaseModel):
    source_id: int
    received: int
    inserted: int
    duplicates: int
    mismatched: int


class Me(BaseModel):
    id: int
    email: str
    display_name: str


class SourceInfo(BaseModel):
    id: int
    folder_path: str
    account: str
    kind: str
    message_count: int
    last_scan_at: datetime | None


class DeleteFolderRequest(StrictModel):
    folder_path: str = Field(min_length=1, max_length=1024)


class DeleteResult(BaseModel):
    deleted_messages: int
    kept_messages: int
