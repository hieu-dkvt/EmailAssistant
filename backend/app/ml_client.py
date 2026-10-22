"""Client gọi ml-service (embedding, NER) trên cùng máy (spec 2026-10-19-ml-service-design).

Text gửi qua 127.0.0.1, không ra ngoài. Lỗi không mang thông điệp gốc (có thể chứa text): chỉ
MlUnavailable / EmbeddingModelMismatch. Không log text.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import embed_model, ml_service_url

EMBED_DIM = 1024
EMBED_MAX_TEXTS = 32
NER_MAX_TEXTS = 64
# Nhãn NER nhận từ ml-service (trùng tên loại che ở app/ai/masking.py); nhãn khác bị bỏ.
NER_LABELS = frozenset({"PERSON", "ORG", "ADDRESS", "PHONE", "EMAIL", "URL", "MONEY"})


class MlUnavailable(Exception):
    """ml-service không chạy, quá thời gian, hoặc trả lỗi."""


class EmbeddingModelMismatch(Exception):
    """ml-service chạy model khác PMM_EMBED_MODEL (spec 18 mục 5)."""

    def __init__(self, got: str, want: str) -> None:
        super().__init__(f"ml-service model={got}, cấu hình PMM_EMBED_MODEL={want}")
        self.got = got
        self.want = want


@dataclass(frozen=True)
class NerEntity:
    start: int
    end: int
    label: str  # một trong NER_LABELS


class MlClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Vector (đã chuẩn hóa) của model hiện hành; khác model → EmbeddingModelMismatch."""
        ...

    async def ner(self, texts: list[str]) -> list[list[NerEntity]]: ...


class HttpMlClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        model: str | None = None,
        timeout: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url or ml_service_url()
        self._model = model or embed_model()
        self._timeout = timeout
        self._transport = transport

    async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,  # không đi qua proxy hệ thống: dữ liệu mail chỉ tới máy này
            ) as client:
                r = await client.post(path, json=body)
        except httpx.HTTPError:
            raise MlUnavailable(f"không gọi được ml-service {path}") from None
        if r.status_code != 200:
            raise MlUnavailable(f"ml-service {path} trả {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            raise MlUnavailable(f"ml-service {path} trả JSON hỏng") from None
        if not isinstance(data, dict):
            raise MlUnavailable(f"ml-service {path} trả JSON sai dạng")
        return data

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_MAX_TEXTS):
            batch = texts[i : i + EMBED_MAX_TEXTS]
            data = await self._post("/embed", {"texts": batch})
            model = data.get("model")
            if model != self._model:
                raise EmbeddingModelMismatch(str(model), self._model)
            vectors = data.get("vectors")
            if (
                not isinstance(vectors, list)
                or len(vectors) != len(batch)
                or any(not isinstance(v, list) or len(v) != EMBED_DIM for v in vectors)
            ):
                raise MlUnavailable("ml-service /embed trả vector sai dạng")
            out.extend(vectors)
        return out

    async def ner(self, texts: list[str]) -> list[list[NerEntity]]:
        out: list[list[NerEntity]] = []
        for i in range(0, len(texts), NER_MAX_TEXTS):
            batch = texts[i : i + NER_MAX_TEXTS]
            data = await self._post("/ner", {"texts": batch})
            rows = data.get("entities")
            models = data.get("models")
            # Fail closed: NER không chạy model nào / trả sai dạng thì không coi là "không có tên".
            if (
                not isinstance(rows, list)
                or len(rows) != len(batch)
                or not isinstance(models, list)
                or not models
                or any(not isinstance(row, list) for row in rows)
            ):
                raise MlUnavailable("ml-service /ner trả kết quả sai dạng")
            for text, row in zip(batch, rows, strict=True):
                ents: list[NerEntity] = []
                for e in row:
                    if not isinstance(e, dict):
                        raise MlUnavailable("ml-service /ner trả kết quả sai dạng")
                    start, end, label = e.get("start"), e.get("end"), e.get("label")
                    if (
                        isinstance(start, int)
                        and isinstance(end, int)
                        and 0 <= start < end <= len(text)
                        and label in NER_LABELS
                    ):
                        ents.append(NerEntity(start, end, label))
                out.append(ents)
        return out


def vector_literal(vector: list[float]) -> str:
    """Vector → chuỗi '[x,y,…]' để bind và CAST(... AS halfvec) trong SQL."""
    return "[" + ",".join(f"{x:.6g}" for x in vector) + "]"
