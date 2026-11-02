"""Rerank bằng cross-encoder (sentence-transformers CrossEncoder), mặc định tắt (spec 27 mục 7).

Chỉ nạp khi ML_RERANK_MODEL được đặt. Text đầu vào không đi đâu ngoài process này.
"""

import threading
from typing import Protocol


class Reranker(Protocol):
    model_name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(
        self, model_name: str, *, max_length: int, batch_size: int, torch_threads: int = 0
    ) -> None:
        import torch
        from sentence_transformers import CrossEncoder

        if torch_threads > 0:
            torch.set_num_threads(torch_threads)
        self.model_name = model_name
        self._batch_size = batch_size
        self._model = CrossEncoder(model_name, device="cpu", max_length=max_length)
        # Một lần suy luận tại một thời điểm, khóa riêng với embedder.
        self._lock = threading.Lock()

    def score(self, query: str, texts: list[str]) -> list[float]:
        pairs = [(query, t) for t in texts]
        with self._lock:
            scores = self._model.predict(
                pairs, batch_size=self._batch_size, show_progress_bar=False
            )
        return [float(x) for x in scores]
