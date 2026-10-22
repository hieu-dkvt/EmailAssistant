"""Mô tả lỗi an toàn cho log và stdout của worker / CLI.

Thông điệp gốc của lỗi không bao giờ được in: lỗi PostgreSQL có DETAIL "Failing row contains (...)"
(cả dòng messages: subject, địa chỉ, body), KeyError có thể chứa Message-ID. Chỉ giữ tên lớp lỗi,
sqlstate, tên ràng buộc và vị trí trong code (giống app/api/errors.py:db_error_handler).
"""

import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


class ProcessingError(RuntimeError):
    """Lỗi đã làm sạch: thông điệp chỉ có mô tả từ describe()."""


def describe(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None) or exc  # DBAPIError của SQLAlchemy bọc lỗi psycopg
    diag = getattr(orig, "diag", None)
    parts = [f"type={type(orig).__name__}"]
    if sqlstate := getattr(orig, "sqlstate", None):
        parts.append(f"sqlstate={sqlstate}")
    if constraint := getattr(diag, "constraint_name", None):
        parts.append(f"constraint={constraint}")
    frames = [
        f
        for f in traceback.extract_tb(exc.__traceback__)
        if Path(f.filename).is_relative_to(APP_DIR)
    ]
    if frames:
        last = frames[-1]
        parts.append(
            f"at={Path(last.filename).relative_to(APP_DIR.parent).as_posix()}:{last.lineno}"
        )
    return " ".join(parts)


def sanitized(context: str, exc: BaseException) -> ProcessingError:
    """Lỗi thay thế để raise ... from None (traceback không còn lỗi gốc và thông điệp của nó)."""
    return ProcessingError(f"{context} {describe(exc)}")
