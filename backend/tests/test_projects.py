"""Test hàm khớp bộ lọc dự án (spec 2026-10-31-project-settings-design.md mục 3). Dữ liệu giả."""

import pytest

from app.projects import MailFields, ProjectRule, addresses, match_projects, normalize


def mail(
    frm: str | None = None,
    to: tuple[str, ...] = (),
    cc: tuple[str, ...] = (),
    subject: str | None = None,
) -> MailFields:
    return MailFields(from_addr=frm, to=to, cc=cc, subject=subject)


CUSTOMER = (normalize("@sample-a.example.jp"),)
TAG = (normalize("【サンプルA】"),)
RULE = ProjectRule(1, CUSTOMER, CUSTOMER, CUSTOMER, TAG)


@pytest.mark.parametrize(
    "m, expected",
    [
        (mail(frm="tanaka@sample-a.example.jp", subject="【サンプルA】仕様確認"), {1}),
        (
            mail(
                frm="pm@vn.example.com",
                to=("tanaka@sample-a.example.jp",),
                subject="Re: 【サンプルA】仕様確認",
            ),
            {1},
        ),
        (
            mail(
                frm="pm@vn.example.com",
                cc=("tanaka@sample-a.example.jp",),
                subject="【サンプルA】共有",
            ),
            {1},
        ),
        (mail(frm="tanaka@sample-a.example.jp", subject="【サンプルB】見積"), set()),
        (mail(frm="x@other.example.jp", subject="【サンプルA】"), set()),
        (mail(frm="tanaka@sample-a.example.jp", subject=None), set()),
        (mail(frm=None, to=("tanaka@sample-a.example.jp",), subject="【サンプルA】"), {1}),
    ],
)
def test_rule_table(m: MailFields, expected: set[int]) -> None:
    assert match_projects(m, [RULE]) == expected


def test_all_filters_empty_matches_nothing() -> None:
    rule = ProjectRule(1, (), (), (), ())
    assert match_projects(mail(frm="a@x.example", subject="bất kỳ"), [rule]) == set()


def test_subject_only() -> None:
    rule = ProjectRule(1, (), (), (), ("[sample-1]",))
    assert match_projects(mail(frm="a@x.example", subject="Re: [SAMPLE-1] hỏi"), [rule]) == {1}
    assert match_projects(mail(frm="a@x.example", subject="khác"), [rule]) == set()


def test_address_only_ignores_subject() -> None:
    rule = ProjectRule(1, CUSTOMER, (), (), ())
    assert match_projects(mail(frm="t@sample-a.example.jp", subject="gì cũng được"), [rule]) == {1}
    assert match_projects(mail(frm="t@sample-a.example.jp", subject=None), [rule]) == {1}


def test_address_filter_is_per_field() -> None:
    # Bộ lọc chỉ ở To thì địa chỉ khách ở From không khớp.
    rule = ProjectRule(1, (), CUSTOMER, (), ())
    assert match_projects(mail(frm="t@sample-a.example.jp"), [rule]) == set()
    assert match_projects(mail(to=("t@sample-a.example.jp",)), [rule]) == {1}


def test_multiple_projects() -> None:
    r2 = ProjectRule(2, (), (), (), ("仕様",))
    m = mail(frm="t@sample-a.example.jp", subject="【サンプルA】仕様確認")
    assert match_projects(m, [RULE, r2]) == {1, 2}


def test_full_width_and_case() -> None:
    assert normalize("ＳＡＭＰＬＥ-1") == "sample-1"
    rule = ProjectRule(1, (), (), (), (normalize("[sample-1]"),))
    assert match_projects(mail(subject="［ＳＡＭＰＬＥ-1］ hỏi"), [rule]) == {1}


def test_mail_address_case_not_relied_on() -> None:
    assert match_projects(
        mail(frm="T@SAMPLE-A.example.jp"), [ProjectRule(1, CUSTOMER, (), (), ())]
    ) == {1}


def test_addresses_skips_bad_items() -> None:
    raw = [{"addr": "a@x.example"}, "lạ", {"name": "n"}, {"addr": 5}, None]
    assert addresses(raw) == ("a@x.example",)


def test_addresses_handles_none() -> None:
    assert addresses(None) == ()


def test_addresses_non_list() -> None:
    assert addresses({"addr": "a@x.example"}) == ()
    assert addresses("a@x.example") == ()
