"""Cấp model Claude, tác vụ và giá ước tính (spec 2026-10-19-ai-settings-design mục 3, 4)."""

import os
from dataclasses import dataclass
from typing import Literal

type Tier = Literal["haiku", "sonnet", "opus"]
TIERS: tuple[Tier, ...] = ("haiku", "sonnet", "opus")


@dataclass(frozen=True)
class TierInfo:
    tier: Tier
    label: str
    default_model: str
    # USD / 1 triệu token (bảng giá 2026-10-06); chỉ để ước tính, giá thật theo hóa đơn Anthropic.
    input_price: float
    output_price: float
    cache_read_price: float
    # Server-side fallback khi model từ chối (refusal): Haiku không có.
    fallbacks: bool


TIER_INFO: dict[Tier, TierInfo] = {
    "haiku": TierInfo("haiku", "Haiku (nhanh, rẻ)", "claude-haiku-5-5", 0.10, 0.50, 0.01, False),
    "sonnet": TierInfo("sonnet", "Sonnet (cân bằng)", "claude-sonnet-5-5", 2.00, 10.00, 0.20, True),
    "opus": TierInfo(
        "opus", "Opus (tốt nhất, đắt nhất)", "claude-opus-5-5", 4.00, 20.00, 0.20, True
    ),
}


def model_for(tier: Tier) -> str:
    """Model của cấp; PMM_MODEL_<CẤP> ghi đè (khi Anthropic ra model mới)."""
    return os.environ.get(f"PMM_MODEL_{tier.upper()}") or TIER_INFO[tier].default_model


def tier_of_model(model: str) -> Tier | None:
    for tier in TIERS:
        if model == model_for(tier) or model == TIER_INFO[tier].default_model:
            return tier
    for tier in TIERS:
        if tier in model:
            return tier
    return None


type Effort = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Task:
    task: str
    label: str
    description: str
    default_tier: Tier
    effort: Effort
    max_tokens: int


TASKS: dict[str, Task] = {
    t.task: t
    for t in (
        Task(
            "question_analysis",
            "Phân tích câu hỏi",
            "Tách bộ lọc (người gửi, khoảng ngày), từ khóa và dịch câu hỏi sang tiếng Nhật "
            "để tìm mail.",
            "haiku",
            "low",
            2_000,
        ),
        Task(
            "answer",
            "Trả lời câu hỏi",
            "Đọc các đoạn mail tìm được và trả lời bằng tiếng Việt, kèm nguồn.",
            "sonnet",
            "medium",
            8_000,
        ),
    )
}


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int
) -> float | None:
    """USD ước tính theo bảng giá của cấp chứa model; model lạ → None."""
    tier = tier_of_model(model)
    if tier is None:
        return None
    info = TIER_INFO[tier]
    return (
        input_tokens * info.input_price
        + output_tokens * info.output_price
        + cache_read * info.cache_read_price
        + cache_write * info.input_price * 1.25
    ) / 1_000_000
