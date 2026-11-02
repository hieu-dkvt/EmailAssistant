"""Client gọi ml-service (embedding) trên cùng máy (spec 2026-10-19-ml-service-design).

Text gửi qua 127.0.0.1, không ra ngoài. Lỗi không mang thông điệp gốc (có thể chứa text): chỉ
MlUnavailable / EmbeddingModelMismatch. Không log text.
"""

import math
from typing import Protocol

import httpx

from app.config import embed_model, ml_service_url

EMBED_DIM = 1024
EMBED_MAX_TEXTS = 32
RERANK_MAX_TEXTS = 64  # giới hạn của ml-service /rerank
RERANK_MAX_QUERY_CHARS = 2_000
RERANK_MAX_TEXT_CHARS = 10_000
RERANK_TIMEOUT_SECONDS = 30.0  # ngắn hơn timeout mặc định: rerank lỗi thì /ask dùng thứ tự RRF


class MlUnavailable(Exception):
    """ml-service không chạy, quá thời gian, hoặc trả lỗi."""


class EmbeddingModelMismatch(Exception):
    """ml-service chạy model khác PMM_EMBED_MODEL (spec 18 mục 5)."""

    def __init__(self, got: str, want: str) -> None:
        super().__init__(f"ml-service model={got}, cấu hình PMM_EMBED_MODEL={want}")
        self.got = got
        self.want = want


class MlClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Vector (đã chuẩn hóa) của model hiện hành; khác model → EmbeddingModelMismatch."""
        ...

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        """Điểm liên quan của từng text với query, cùng thứ tự `texts`. Lỗi → MlUnavailable."""
        ...


def _finite(x: object) -> bool:
    if isinstance(x, bool) or not isinstance(x, int | float):
        return False
    try:
        return math.isfinite(x)
    except OverflowError:  # số nguyên quá lớn cho float
        return False


def check_scores(scores: object, expected: int) -> list[float]:
    """Điểm hợp lệ: đúng số lượng, số hữu hạn. Sai → MlUnavailable (thông điệp không chứa text)."""
    if not isinstance(scores, list) or len(scores) != expected or not all(map(_finite, scores)):
        raise MlUnavailable("ml-service /rerank trả điểm sai dạng")
    return [float(x) for x in scores]


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

    async def _post(
        self, path: str, body: dict[str, object], timeout: float | None = None
    ) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout if timeout is None else timeout,
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

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not texts or len(texts) > RERANK_MAX_TEXTS:
            raise MlUnavailable("rerank: số text ngoài giới hạn")
        data = await self._post(
            "/rerank",
            {
                "query": query[:RERANK_MAX_QUERY_CHARS],
                "texts": [t[:RERANK_MAX_TEXT_CHARS] for t in texts],
            },
            timeout=RERANK_TIMEOUT_SECONDS,
        )
        return check_scores(data.get("scores"), len(texts))


def vector_literal(vector: list[float]) -> str:
    """Vector → chuỗi '[x,y,…]' để bind và CAST(... AS halfvec) trong SQL."""
    return "[" + ",".join(f"{x:.6g}" for x in vector) + "]"
