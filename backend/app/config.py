"""Cấu hình kết nối DB, đọc từ biến môi trường.

Mỗi role DB có URL riêng: owner (migration), api (phục vụ người dùng, chịu RLS),
worker (job nền). Không có biến môi trường thì dùng URL dev trỏ tới container
docker compose trên máy (127.0.0.1:15432, mật khẩu dev).
"""

import os
from typing import Literal
from urllib.parse import urlparse

type DbRole = Literal["owner", "api", "worker"]

_DEV_DEFAULTS: dict[DbRole, str] = {
    "owner": "postgresql+psycopg://app_owner:dev-owner@127.0.0.1:15432/mailassist",
    "api": "postgresql+psycopg://app_api:dev-api@127.0.0.1:15432/mailassist",
    "worker": "postgresql+psycopg://app_worker:dev-worker@127.0.0.1:15432/mailassist",
}


def database_url(role: DbRole) -> str:
    """URL kết nối cho role; biến DATABASE_URL_<ROLE> ghi đè giá trị dev."""
    return os.environ.get(f"DATABASE_URL_{role.upper()}") or _DEV_DEFAULTS[role]


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def ml_service_url() -> str:
    """ml-service (embedding) chạy trên cùng máy (spec 2026-10-19-ml-service-design).

    ml-service nhận text mail gốc: chỉ cho phép http tới chính máy này, máy khác phải là https.
    """
    url = (os.environ.get("ML_SERVICE_URL") or "http://127.0.0.1:8001").rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme == "https" or (
        parsed.scheme == "http" and (parsed.hostname or "") in LOOPBACK_HOSTS
    ):
        return url
    raise ValueError("ML_SERVICE_URL phải là http://127.0.0.1… (cùng máy) hoặc https://…")


def embed_model() -> str:
    """Model embedding hiện hành. ml-service chạy model khác thì không embed, không tìm (spec 18
    mục 5): đổi model nghĩa là embed lại toàn bộ, phải hỏi người dùng trước."""
    return os.environ.get("PMM_EMBED_MODEL") or "BAAI/bge-m3"


def rerank_enabled() -> bool:
    """Rerank nguồn của /ask qua ml-service `/rerank` (spec 27 mục 7). Mặc định tắt: chỉ bật khi
    người dùng đặt PMM_RERANK=1 sau khi có số liệu eval."""
    return os.environ.get("PMM_RERANK") == "1"
