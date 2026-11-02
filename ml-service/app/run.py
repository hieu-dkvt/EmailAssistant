"""Chạy ml-service: uv run --directory ml-service python -m app.run [--port 8001]

Chỉ lắng nghe 127.0.0.1. Lần chạy đầu tải model từ Hugging Face (bge-m3 khoảng 2,3 GB) vào cache
cục bộ; các lần sau model đã có trong cache thì tự đặt HF_HUB_OFFLINE=1 (không gọi Hugging Face).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Tắt telemetry của Hugging Face trước khi import thư viện (chỉ gửi thông tin tải model, nhưng
# không cần).
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def model_in_cache(repo_id: str) -> bool:
    """Cache Hugging Face (HF_HUB_CACHE hoặc HF_HOME/hub) đã có config.json của model chưa."""
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path(home) / "hub")
    snapshots = hub / ("models--" + repo_id.replace("/", "--")) / "snapshots"
    return any(snapshots.glob("*/config.json"))


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.run")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    import uvicorn

    from app.config import Settings
    from app.embedder import SentenceTransformerEmbedder
    from app.main import create_app

    settings = Settings()
    # Model đã có trong cache cục bộ thì không hỏi Hugging Face nữa (chỉ lần đầu mới tải).
    # Kiểm bằng đường dẫn file, không import huggingface_hub: thư viện đọc HF_HUB_OFFLINE một lần
    # lúc import, đặt biến sau đó không còn tác dụng.
    if "HF_HUB_OFFLINE" not in os.environ and model_in_cache(settings.embed_model):
        os.environ["HF_HUB_OFFLINE"] = "1"
        logging.getLogger("ml").info("model trong cache: HF_HUB_OFFLINE=1")
    app = create_app(
        lambda: SentenceTransformerEmbedder(
            settings.embed_model,
            max_seq_length=settings.max_seq_length,
            batch_size=settings.batch_size,
            torch_threads=settings.torch_threads,
        ),
        settings,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
