"""Chuẩn hóa subject để gom thread (spec 2026-10-17-threading-design.md mục 5)."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

type PrefixKind = Literal["reply", "forward"]

# Prefix dài đặt trước (fwd trước fw) cho dễ đọc; regex tự quay lui nên thứ tự không đổi kết quả.
REPLY_PREFIXES = ("返信", "回复", "答复", "re", "aw", "sv", "tl")
FORWARD_PREFIXES = ("転送", "转发", "fwd", "fw", "wg", "tr", "rv", "ct")
URGENCY_TAGS = (
    "至急",
    "緊急",
    "重要",
    "要返信",
    "要確認",
    "再々送",
    "再送",
    "訂正",
    "修正版",
    "修正",
    "urgent",
    "important",
    "gấp",
    "khẩn",
    "quan trọng",
)

_PREFIX_RE = re.compile(
    r"(?P<p>" + "|".join(REPLY_PREFIXES + FORWARD_PREFIXES) + r")\s*(?:\[\d+\]|\(\d+\))?\s*:\s*",
    re.IGNORECASE,
)
# Sau NFKC: ［］ → [], （） → (); 【】 và 〔〕 giữ nguyên.
_TAG_RE = re.compile(
    r"[\[【〔(]\s*(?:" + "|".join(map(re.escape, URGENCY_TAGS)) + r")\s*[\]】〕)]\s*",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class NormalizedSubject:
    text: str  # đã bỏ prefix và thẻ mức độ, casefold
    outer: PrefixKind | None  # loại của prefix ngoài cùng (prefix đầu tiên bị bỏ)


def normalize_subject(subject: str | None) -> NormalizedSubject:
    s = _SPACES.sub(" ", unicodedata.normalize("NFKC", subject or "")).strip()
    outer: PrefixKind | None = None
    while True:
        m = _PREFIX_RE.match(s)
        if m:
            if outer is None:
                outer = "reply" if m["p"].lower() in REPLY_PREFIXES else "forward"
            s = s[m.end() :]
            continue
        m = _TAG_RE.match(s)
        if m:
            s = s[m.end() :]
            continue
        break
    return NormalizedSubject(s.strip().casefold(), outer)
