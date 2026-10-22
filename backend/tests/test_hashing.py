import hashlib
from datetime import UTC, datetime, timedelta, timezone

from app.mail.hashing import message_id_hash, normalize_message_id


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_normalize_message_id() -> None:
    assert normalize_message_id(" <Abc@Example.COM> ") == "Abc@Example.COM"
    assert normalize_message_id("<>") is None
    assert normalize_message_id("   ") is None
    assert normalize_message_id(None) is None


def test_hash_with_message_id_is_case_insensitive() -> None:
    h = message_id_hash("<Abc@Example.COM>", None, None, None, "")
    assert h == sha("mid:abc@example.com")
    assert h == message_id_hash("abc@example.com", "x@y", None, "khác", "khác")


def test_hash_without_message_id_uses_fallback_fields() -> None:
    sent = datetime(2026, 10, 17, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    body = "あ" * 300
    h = message_id_hash(None, "Tanaka@Client.example.jp", sent, "件名", body)
    expected = sha(
        "nomid:tanaka@client.example.jp\n"
        + sent.astimezone(UTC).isoformat()
        + "\n件名\n"
        + "あ" * 200
    )
    assert h == expected
    assert message_id_hash(None, None, None, None, "") == sha("nomid:\n\n\n")
