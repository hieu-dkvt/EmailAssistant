"""Embedding dense bằng sentence-transformers (mặc định BAAI/bge-m3, 1024 chiều, cosine).

Model tải từ Hugging Face lúc khởi động (lần đầu) rồi nằm trong cache cục bộ; text đầu vào không
đi đâu ngoài process này. Vector đã chuẩn hóa (độ dài 1): backend dùng khoảng cách cosine.
"""

import threading
from typing import Protocol


class Embedder(Protocol):
    model_name: str
    dim: int
    max_seq_length: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(
        self, model_name: str, *, max_seq_length: int, batch_size: int, torch_threads: int = 0
    ) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        if torch_threads > 0:
            torch.set_num_threads(torch_threads)
        self.model_name = model_name
        self._batch_size = batch_size
        self._model = SentenceTransformer(model_name, device="cpu")
        self._model.max_seq_length = max_seq_length
        self.max_seq_length = max_seq_length
        get_dim = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        self.dim = int(get_dim() or 0)
        # Một lần suy luận tại một thời điểm: giới hạn bộ nhớ trên máy ít RAM.
        self._lock = threading.Lock()

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            vectors = self._model.encode(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return [[float(x) for x in row] for row in vectors]
