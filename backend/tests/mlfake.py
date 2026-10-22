"""ml-service và Claude giả cho test (không tải model, không gọi mạng).

FakeMl: vector theo "khái niệm": text chứa từ của khái niệm i (bất kể ngôn ngữ) → chiều i. Nhờ
vậy câu tiếng Việt "báo giá" gần mail tiếng Nhật "見積" như embedding đa ngôn ngữ thật.
"""

import math
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.ai.client import LlmResponse, LlmUsage
from app.config import embed_model
from app.ml_client import EMBED_DIM, EmbeddingModelMismatch, MlUnavailable, NerEntity

CONCEPTS: list[tuple[str, ...]] = [
    ("見積", "báo giá", "quote", "estimate"),
    ("障害", "エラー", "sự cố", "lỗi", "error", "incident"),
    ("請求", "hóa đơn", "invoice"),
    ("会議", "họp", "meeting"),
    ("仕様", "đặc tả", "spec"),
]


def concept_vector(text: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    low = text.lower()
    for i, words in enumerate(CONCEPTS):
        if any(w.lower() in low for w in words):
            v[i] = 1.0
    if not any(v):
        v[EMBED_DIM - 1] = 1.0
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


@dataclass
class FakeMl:
    model: str = field(default_factory=embed_model)
    down: bool = False
    ner_down: bool = False
    ner_names: tuple[str, ...] = ()  # chuỗi NER "nhận ra" là PERSON
    embedded: list[str] = field(default_factory=list)
    ner_inputs: list[str] = field(default_factory=list)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.down:
            raise MlUnavailable("down")
        if self.model != embed_model():
            raise EmbeddingModelMismatch(self.model, embed_model())
        self.embedded += texts
        return [concept_vector(t) for t in texts]

    async def ner(self, texts: list[str]) -> list[list[NerEntity]]:
        if self.ner_down:
            raise MlUnavailable("down")
        self.ner_inputs += texts
        out: list[list[NerEntity]] = []
        for t in texts:
            ents: list[NerEntity] = []
            for name in self.ner_names:
                start = t.find(name)
                while start != -1:
                    ents.append(NerEntity(start, start + len(name), "PERSON"))
                    start = t.find(name, start + len(name))
            out.append(ents)
        return out


@dataclass
class FakeLlm:
    """Trả lời theo hàng đợi responses; ghi lại mọi request (để kiểm tra nội dung đã che)."""

    responses: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> LlmResponse:
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LlmResponse):
            return item
        assert isinstance(item, BaseModel)
        return LlmResponse(
            text=item.model_dump_json(),
            model=kwargs["model"],
            stop_reason="end_turn",
            usage=LlmUsage(input_tokens=100, output_tokens=20),
        )

    def sent_text(self) -> str:
        return "\n".join(f"{r['system']}\n{r['user']}" for r in self.requests)
