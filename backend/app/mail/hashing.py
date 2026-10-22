"""Khóa chống trùng của mail (message_id_hash).

Spec: docs/superpowers/specs/2026-10-17-ingest-api-design.md mục 4.

Phạm vi chống trùng là một người dùng: khóa trong DB là (user_id, message_id_hash).
"""

import hashlib
from datetime import UTC, datetime

FALLBACK_BODY_CHARS = 200


def normalize_message_id(message_id: str | None) -> str | None:
    """Bỏ khoảng trắng và cặp < > bao ngoài; rỗng thì None."""
    s = (message_id or "").strip()
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()
    return s or None


def message_id_hash(
    message_id: str | None,
    from_addr: str | None,
    sent_at: datetime | None,
    subject: str | None,
    body_text: str,
) -> str:
    mid = normalize_message_id(message_id)
    if mid is not None:
        key = "mid:" + mid.lower()
    else:
        date = sent_at.astimezone(UTC).isoformat() if sent_at else ""
        key = "\n".join(
            [
                "nomid:" + (from_addr or "").lower(),
                date,
                subject or "",
                body_text[:FALLBACK_BODY_CHARS],
            ]
        )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
