"""Tác vụ AI (spec 2026-10-19-ai-settings-design mục 3; bỏ ba cấp model ở spec 28)."""

from dataclasses import dataclass
from typing import Literal

type Effort = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Task:
    task: str
    label: str
    description: str
    effort: Effort
    max_tokens: int


TASK_TRANSLATE = "translate"

TASKS: dict[str, Task] = {
    t.task: t
    for t in (
        Task(
            "question_analysis",
            "Phân tích câu hỏi",
            "Tách bộ lọc (người gửi, khoảng ngày), từ khóa và dịch câu hỏi sang tiếng Nhật "
            "để tìm mail.",
            "low",
            2_000,
        ),
        Task(
            "answer",
            "Trả lời câu hỏi",
            "Đọc các đoạn mail tìm được và trả lời bằng tiếng Việt, kèm nguồn.",
            "medium",
            8_000,
        ),
        Task(
            TASK_TRANSLATE,
            "Dịch mail",
            "Dịch mail của khách sang tiếng Việt, giữ thuật ngữ theo glossary.",
            "low",
            16_000,
        ),
    )
}
