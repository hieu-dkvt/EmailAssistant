"""Domain khách của dự án và nhận diện folder Inbox (spec 26). Hàm thuần, không regex."""

import unicodedata
from collections.abc import Sequence

MAX_DOMAIN_LEN = 253


def normalize_domain(value: str) -> str:
    """NFKC + casefold, bỏ `@` đầu. ValueError (thông báo cố định) khi không phải domain."""
    if len(value) > MAX_DOMAIN_LEN + 8:  # chặn sớm trước khi chuẩn hóa giá trị quá lớn
        raise ValueError("Domain không hợp lệ")
    domain = unicodedata.normalize("NFKC", value).strip().casefold()
    domain = domain.removeprefix("@")
    if not domain or len(domain) > MAX_DOMAIN_LEN or "." not in domain:
        raise ValueError("Domain không hợp lệ")
    if any(c.isspace() or c == "@" or unicodedata.category(c) == "Cc" for c in domain):
        raise ValueError("Domain không hợp lệ")
    if any(not label for label in domain.split(".")):
        raise ValueError("Domain không hợp lệ")
    return domain


def domain_matches(addr: str | None, domains: Sequence[str]) -> bool:
    """Địa chỉ thuộc domain khi phần sau `@` cuối bằng d hoặc kết thúc bằng `.d`."""
    if not addr or "@" not in addr:
        return False
    host = addr.rsplit("@", 1)[1].strip().casefold()
    return any(host == d or host.endswith("." + d) for d in domains)


def is_inbox_path(path: str) -> bool:
    return path.rsplit("/", 1)[-1].casefold() == "inbox"


def inbox_path_sql(column: str) -> str:
    """Điều kiện SQL tương đương is_inbox_path. column là tên cột cố định trong code."""
    return f"lower(regexp_replace({column}, '^.*/', '')) = 'inbox'"
