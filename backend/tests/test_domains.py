"""Chuẩn hóa và khớp domain khách (spec 26). Dữ liệu giả *.example.*."""

import pytest

from app.mail.domains import domain_matches, is_inbox_path, normalize_domain


def test_normalize_domain() -> None:
    assert normalize_domain("@Sample-A.Example.JP ") == "sample-a.example.jp"
    assert normalize_domain("＠sample.example.jp") == "sample.example.jp"


@pytest.mark.parametrize("bad", ["", "nodot", "a b.jp", "x" * 254 + ".jp", "@", "a@b.jp"])
def test_normalize_domain_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_domain(bad)


def test_domain_matches() -> None:
    assert domain_matches("t@mail.sample-a.example.jp", ["sample-a.example.jp"])
    assert domain_matches("t@SAMPLE-A.example.jp", ["sample-a.example.jp"])
    assert not domain_matches("t@xsample-a.example.jp", ["sample-a.example.jp"])
    assert not domain_matches(None, ["sample-a.example.jp"])
    assert not domain_matches("no-at-sign", ["sample-a.example.jp"])
    assert not domain_matches("t@sample-a.example.jp", [])


def test_is_inbox_path() -> None:
    assert is_inbox_path("ImapMail/imap.example.com/INBOX")
    assert is_inbox_path("Mail/Local Folders/Inbox")
    assert not is_inbox_path("ImapMail/imap.example.com/INBOX.sbd/ProjectA")
