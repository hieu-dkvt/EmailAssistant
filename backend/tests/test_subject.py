import pytest

from app.mail.subject import normalize_subject


@pytest.mark.parametrize(
    ("subject", "text", "outer"),
    [
        ("Re: Báo giá", "báo giá", "reply"),
        ("RE[2]: RE: Fw: Báo giá", "báo giá", "reply"),
        ("Re(3): báo giá", "báo giá", "reply"),
        ("Fwd: Re: 見積り", "見積り", "forward"),
        ("FW : 見積り", "見積り", "forward"),
        ("ＲＥ：　見積り", "見積り", "reply"),
        ("返信: 見積り", "見積り", "reply"),
        ("転送: 見積り", "見積り", "forward"),
        ("AW: WG: Angebot", "angebot", "reply"),
        ("TL: Kế hoạch", "kế hoạch", "reply"),
        ("Re: 【至急】入庫エラー", "入庫エラー", "reply"),
        ("【至急】Re: 入庫エラー", "入庫エラー", "reply"),
        ("[重要] ［再送］ 請求書", "請求書", None),
        ("〔訂正〕Re: 議事録", "議事録", "reply"),
        ("Re: 【サンプルA】在庫管理", "【サンプルa】在庫管理", "reply"),
        ("[Project-X] Kickoff", "[project-x] kickoff", None),
        ("Report: weekly", "report: weekly", None),
        ("Re:", "", "reply"),
        ("  Sprint   12  planning ", "sprint 12 planning", None),
        ("Re:　Sprint　12", "sprint 12", "reply"),
        (None, "", None),
        ("", "", None),
    ],
)
def test_normalize_subject(subject: str | None, text: str, outer: str | None) -> None:
    n = normalize_subject(subject)
    assert (n.text, n.outer) == (text, outer)


def test_urgency_tag_without_prefix_is_not_a_reply() -> None:
    assert normalize_subject("【至急】入庫エラー").outer is None
