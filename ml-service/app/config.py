"""Cấu hình ml-service, đọc từ biến môi trường (spec 2026-10-19-ml-service-design.md mục 4)."""

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _hosts() -> list[str]:
    raw = os.environ.get("ML_ALLOWED_HOSTS", "127.0.0.1,localhost")
    return [h.strip() for h in raw.split(",") if h.strip()]


@dataclass(frozen=True)
class Settings:
    # Tên model là một phần dữ liệu: backend lưu kèm mỗi embedding. Đổi model = embed lại toàn bộ.
    embed_model: str = field(
        default_factory=lambda: os.environ.get("ML_EMBED_MODEL", "BAAI/bge-m3")
    )
    # Số token tối đa mỗi đoạn; backend cắt đoạn ngắn hơn mức này (spec 18 mục 3).
    max_seq_length: int = field(default_factory=lambda: _int("ML_MAX_SEQ_LENGTH", 512))
    batch_size: int = field(default_factory=lambda: _int("ML_BATCH_SIZE", 8))
    # Số luồng CPU cho PyTorch; 0 = để PyTorch tự chọn.
    torch_threads: int = field(default_factory=lambda: _int("ML_TORCH_THREADS", 0))
    ner_ja_model: str = field(default_factory=lambda: os.environ.get("ML_NER_JA", "ja_ginza"))
    ner_en_model: str = field(default_factory=lambda: os.environ.get("ML_NER_EN", "en_core_web_sm"))
    allowed_hosts: list[str] = field(default_factory=_hosts)
