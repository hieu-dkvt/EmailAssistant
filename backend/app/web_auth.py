"""Mật khẩu và phiên đăng nhập web (spec 2026-10-18-web-login-design.md mục 4).

Mật khẩu băm bằng scrypt của thư viện chuẩn. Token phiên chỉ lưu SHA-256 (như token collector).
Bộ đếm đăng nhập sai nằm trong bộ nhớ của tiến trình API (giai đoạn một người dùng).
"""

import base64
import hashlib
import hmac
import secrets
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

SESSION_PREFIX = "pmw_"
SESSION_TTL = timedelta(hours=12)
SESSION_COOKIE = "pmm_session"
MIN_PASSWORD_LENGTH = 12

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16
KEY_BYTES = 32


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    # NFC: chữ có dấu gõ dạng dựng sẵn ("ậ") hay tổ hợp ("a" + dấu) đều cho cùng một mật khẩu.
    data = unicodedata.normalize("NFC", password).encode("utf-8")
    return hashlib.scrypt(data, salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=KEY_BYTES)


def password_problem(password: str) -> str | None:
    """Lý do mật khẩu mới không dùng được (None: dùng được).

    Ký tự ẩn/điều khiển (BOM, ký tự thay thế, tab…) thường là do shell chèn vào khi truyền mật khẩu
    qua pipe; người dùng không gõ được chúng trên trình duyệt nên sẽ không bao giờ đăng nhập được.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    if any(ch == "�" or unicodedata.category(ch)[0] == "C" for ch in password):
        return "Mật khẩu có ký tự ẩn hoặc ký tự điều khiển (thường do shell chèn khi dùng pipe)."
    return None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Chuỗi băm hỏng hoặc tham số lạ → False (không lỗi)."""
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        n_, r_, p_ = int(n), int(r), int(p)
        if n_ > SCRYPT_N or r_ > SCRYPT_R or p_ > SCRYPT_P:  # không để chuỗi lạ làm tốn bộ nhớ
            return False
        expected = base64.b64decode(key, validate=True)
        actual = _scrypt(password, base64.b64decode(salt, validate=True), n_, r_, p_)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


_dummy_hashes: dict[int, str] = {}


def dummy_hash() -> str:
    """Hash giả theo tham số scrypt hiện hành: email không có / chưa có mật khẩu vẫn tốn thời gian
    như khi sai mật khẩu. Tạo lúc dùng lần đầu (không tốn 0,6 giây khi import)."""
    if SCRYPT_N not in _dummy_hashes:
        _dummy_hashes[SCRYPT_N] = hash_password(secrets.token_urlsafe(16))
    return _dummy_hashes[SCRYPT_N]


def new_session_token() -> str:
    return SESSION_PREFIX + secrets.token_urlsafe(32)


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


@dataclass
class _Attempts:
    first_failure: float = 0.0
    failures: int = 0
    in_flight: int = 0  # lần thử đang kiểm tra mật khẩu


@dataclass
class LoginThrottle:
    """Tối đa `max_failures` lần sai cho một email trong `window` giây (tính từ lần sai đầu).

    Mỗi lần thử giữ chỗ (`begin`) TRƯỚC khi băm mật khẩu: lần thử đang chạy cũng được tính, nên gửi
    hàng trăm request cùng lúc không vượt được giới hạn (review bảo mật 2026-10-19).
    """

    max_failures: int = 5
    window: float = 15 * 60
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, _Attempts] = field(default_factory=dict)

    def begin(self, email: str) -> bool:
        """Giữ chỗ cho một lần thử; False khi đã hết lượt (lần sai + lần đang chạy ≥ giới hạn)."""
        entry = self._current(email)
        if entry.failures + entry.in_flight >= self.max_failures:
            return False
        entry.in_flight += 1
        self._entries[email] = entry
        if len(self._entries) > 10_000:  # không để bộ nhớ tăng mãi theo email lạ
            self._prune()
        return True

    def finish(self, email: str, ok: bool) -> None:
        entry = self._entries.get(email)
        if entry is None:
            return
        entry.in_flight = max(0, entry.in_flight - 1)
        if ok:
            entry.failures = 0
        else:
            if entry.failures == 0:
                entry.first_failure = self.clock()
            entry.failures += 1
        if entry.failures == 0 and entry.in_flight == 0:
            del self._entries[email]

    def blocked(self, email: str) -> bool:
        return self._current(email).failures >= self.max_failures

    def _expired(self, entry: _Attempts, now: float) -> bool:
        return entry.failures > 0 and now - entry.first_failure >= self.window

    def _current(self, email: str) -> _Attempts:
        entry = self._entries.get(email)
        if entry is None:
            return _Attempts()
        if self._expired(entry, self.clock()):
            entry.failures = 0
        return entry

    def _prune(self) -> None:
        now = self.clock()
        for key, entry in list(self._entries.items()):
            if entry.in_flight == 0 and (entry.failures == 0 or self._expired(entry, now)):
                del self._entries[key]
