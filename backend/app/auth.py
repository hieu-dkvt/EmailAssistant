"""Token của collector: sinh token và băm để lưu/tra cứu (không lưu token gốc)."""

import hashlib
import secrets

TOKEN_PREFIX = "pmm_"


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()
