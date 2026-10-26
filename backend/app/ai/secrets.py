"""Mã hóa API key của nhà cung cấp AI (spec 2026-10-25-ai-provider-design mục 5).

Fernet, khóa trong biến môi trường PMM_SECRET_KEY (sinh bằng `python -m app.cli gen-secret-key`).
Khóa sai hoặc đổi khóa thì giải mã trả None (coi như chưa có key), không ném lỗi.
"""

import os

from cryptography.fernet import Fernet, InvalidToken


class SecretKeyMissing(Exception):
    """Chưa đặt PMM_SECRET_KEY (hoặc giá trị không phải khóa Fernet hợp lệ)."""


def _fernet() -> Fernet | None:
    raw = os.environ.get("PMM_SECRET_KEY")
    if not raw:
        return None
    try:
        return Fernet(raw.encode())
    except ValueError:
        return None


def secret_key_ready() -> bool:
    return _fernet() is not None


def encrypt(plain: str) -> str:
    f = _fernet()
    if f is None:
        raise SecretKeyMissing
    return f.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def generate_key() -> str:
    return Fernet.generate_key().decode()
