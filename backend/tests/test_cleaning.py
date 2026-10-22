"""Làm sạch nội dung (spec 2026-10-17-content-cleaning-design.md). Mail tự bịa (*.example.*)."""

import time
from textwrap import dedent

import pytest

from app.mail.cleaning import CleanResult, apply_cuts, clean_body


def body(s: str) -> str:
    return dedent(s).lstrip("\n")


CASES: list[tuple[str, str | None, str, str, list[str]]] = [
    # (tên, subject, body, body_clean mong đợi, loại đoạn cắt theo thứ tự)
    (
        "gmail_jp_top_post",
        "Re: 仕様書",
        body("""
            田中様

            お世話になっております。チャンです。
            承知しました。明日までに送付します。

            --
            チャン・ヒエウ
            VN Outsource Co., Ltd.

            2026年10月1日(水) 10:00 田中 健二 <tanaka@client-a.example.jp>:

            > チャン様
            >
            > 仕様書をお送りください。
            """),
        "田中様\n\nお世話になっております。チャンです。\n承知しました。明日までに送付します。",
        ["signature", "quote"],
    ),
    (
        "gmail_en_wrapped_attribution",
        "Re: Renewal",
        body("""
            Thanks Brian, we will confirm by end of October.

            On Wed, Oct 1, 2026 at 10:00 AM Brian Carter <
            brian@vendor-c.example.com> wrote:

            > Hi Hieu,
            > Updated quote attached.
            """),
        "Thanks Brian, we will confirm by end of October.",
        ["quote"],
    ),
    (
        "gmail_vn",
        "Re: Báo cáo",
        body("""
            Anh An xem giúp nhé.

            Vào Th 4, 1 thg 10, 2026 vào lúc 10:00 Nguyễn Văn An <an.nv@vn-outsource.example.com> đã viết:
            > Em gửi báo cáo.
            """),  # noqa: E501
        "Anh An xem giúp nhé.",
        ["quote"],
    ),
    (
        "thunderbird_jp",
        "Re: 見積り",
        body("""
            承知しました。

            2026/10/01 10:00 に 田中 健二 さんは書きました:
            > お見積りをお願いします。
            """),
        "承知しました。",
        ["quote"],
    ),
    (
        "outlook_en_header_block",
        "RE: Renewal",
        body("""
            Hi Brian,

            Please see below.

            Best regards,
            Hieu

            ________________________________
            From: Brian Carter <brian@vendor-c.example.com>
            Sent: Wednesday, October 1, 2026 10:00 AM
            To: Hieu <pm@vn-outsource.example.com>
            Subject: RE: Renewal

            Hi Hieu,
            The quote is attached.
            """),
        "Hi Brian,\n\nPlease see below.\n\nBest regards,\nHieu",
        ["quote"],
    ),
    (
        "outlook_original_message_jp_headers",
        "RE: テスト結果",
        body("""
            佐藤様

            確認しました。

            -----Original Message-----
            差出人: 佐藤 由紀 <sato@client-a.example.jp>
            送信日時: 2026年10月1日 10:00
            宛先: pm@vn-outsource.example.com
            件名: テスト結果

            テスト結果を送ります。
            """),
        "佐藤様\n\n確認しました。",
        ["quote"],
    ),
    (
        "outlook_jp_bold_header_block",
        "RE: 議事録",
        body("""
            了解です。

            -----元のメッセージ-----
            *差出人:* 佐藤 由紀
            *送信日時:* 2026年10月1日 10:00
            *件名:* 議事録
            議事録です。
            """),
        "了解です。",
        ["quote"],
    ),
    (
        "inline_reply_with_attribution",
        "Re: 帳票",
        body("""
            田中様

            2026年10月1日(水) 10:00 田中 健二 <tanaka@client-a.example.jp>:
            > 帳票は全部で12種類です。

            承知しました。

            > 納期は11月末です。

            問題ありません。

            --
            チャン
            """),
        "田中様\n\n承知しました。\n\n問題ありません。",
        ["quote", "quote", "signature"],
    ),
    (
        "inline_without_attribution",
        "Re: Regression",
        body("""
            Bình,

            > Kết quả regression: 212 case, fail 7.
            Em gửi anh An xử lý giúp anh.
            > Chi tiết 7 case lỗi
            OK.
            """),
        "Bình,\n\nEm gửi anh An xử lý giúp anh.\nOK.",
        ["quote", "quote"],
    ),
    (
        "gmail_forward_kept",
        "Fwd: 見積書",
        body("""
            An xem giúp anh nhé.

            ---------- Forwarded message ---------
            From: 田中 健二 <tanaka@client-a.example.jp>
            Date: 2026年10月1日(水) 10:00
            Subject: 見積書
            To: <pm@vn-outsource.example.com>

            チャン様
            見積書をお送りください。
            """),
        "An xem giúp anh nhé.\n\n---------- Forwarded message ---------\n"
        "From: 田中 健二 <tanaka@client-a.example.jp>\nDate: 2026年10月1日(水) 10:00\n"
        "Subject: 見積書\nTo: <pm@vn-outsource.example.com>\n\nチャン様\n見積書をお送りください。",
        [],
    ),
    (
        "outlook_forward_fw_subject_kept",
        "FW: テスト結果",
        body("""
            FYI

            -----Original Message-----
            From: 佐藤 由紀 <sato@client-a.example.jp>
            Sent: Wednesday, October 1, 2026 10:00 AM
            Subject: テスト結果

            テスト結果です。
            """),
        "FYI\n\n-----Original Message-----\nFrom: 佐藤 由紀 <sato@client-a.example.jp>\n"
        "Sent: Wednesday, October 1, 2026 10:00 AM\nSubject: テスト結果\n\nテスト結果です。",
        [],
    ),
    (
        "reply_to_forward_is_quote",
        "Re: Fwd: 見積書",
        body("""
            OK anh.

            2026年10月2日(木) 9:00 Trần Hiếu <pm@vn-outsource.example.com>:
            > An xem giúp anh nhé.
            >
            > ---------- Forwarded message ---------
            > From: 田中 健二 <tanaka@client-a.example.jp>
            """),
        "OK anh.",
        ["quote"],
    ),
    (
        "thunderbird_jp_forward_with_signature",
        "転送: 議事録",
        body("""
            共有します。

            --
            チャン

            -------- 転送メッセージ --------
            件名: 議事録
            日付: 2026/10/01 10:00
            議事録です。
            """),
        "共有します。\n\n-------- 転送メッセージ --------\n件名: 議事録\n日付: 2026/10/01 10:00\n"
        "議事録です。",
        ["signature"],
    ),
    (
        "signature_rule_tel",
        "Re: 確認",
        body("""
            佐藤様

            承知しました。

            ━━━━━━━━━━━━━━━━
            株式会社サンプルA
            品質管理課 佐藤 由紀
            TEL: 03-0000-0002
            ━━━━━━━━━━━━━━━━
            """),
        "佐藤様\n\n承知しました。",
        ["signature"],
    ),
    (
        "mobile_footer_en",
        "Re: Sprint",
        "OK anh.\n\nSent from my iPhone\n",
        "OK anh.",
        ["signature"],
    ),
    (
        "mobile_footer_jp",
        "Re: 確認",
        "了解です。\n\niPhoneから送信\n",
        "了解です。",
        ["signature"],
    ),
    (
        "contact_block_without_rule",
        "見積り",
        body("""
            よろしくお願いいたします。
            株式会社サンプルA
            システム開発部 田中 健二
            TEL: 03-0000-0001
            """),
        "よろしくお願いいたします。",
        ["signature"],
    ),
    (
        "section_rule_without_contact_kept",
        "議事録",
        body("""
            議事録です。

            ==========
            1. 入庫エラー: 修正済み
            2. 帳票No.5: 来週納品
            ==========

            以上です。
            """),
        "議事録です。\n\n==========\n1. 入庫エラー: 修正済み\n2. 帳票No.5: 来週納品\n==========\n\n"
        "以上です。",
        [],
    ),
    (
        "colon_line_without_mail_and_date_kept",
        "Kế hoạch",
        body("""
            Các việc tuần này:
            - Họp lúc 10:00
            - Gửi báo cáo cho a@example.com
            """),
        "Các việc tuần này:\n- Họp lúc 10:00\n- Gửi báo cáo cho a@example.com",
        [],
    ),
    (
        "dashdash_without_content_before_kept",
        "Chữ ký",
        "--\nchỉ có chữ ký\n",
        "--\nchỉ có chữ ký",
        [],
    ),
    (
        "signature_after_quote",
        "Re: 見積り",
        body("""
            田中様

            承知しました。

            2026年9月1日 10:00 田中 健二 <tanaka@client-a.example.jp>:
            > ご担当者様
            > お見積りをお願いできますでしょうか。

            --
            Trần Hiếu
            Project Manager | VN Outsource Co., Ltd.
            Tel: +84-90-000-0001
            """),
        "田中様\n\n承知しました。",
        ["quote", "signature"],
    ),
    (
        "attribution_followed_by_unquoted_text",
        "Re: Renewal",
        body("""
            OK, cảm ơn anh.

            On Wed, Oct 1, 2026 at 10:00 AM Brian Carter <brian@vendor-c.example.com> wrote:
            Hi Hieu,
            Updated quote attached.
            """),
        "OK, cảm ơn anh.",
        ["quote"],
    ),
    (
        "join_does_not_swallow_previous_line",
        "Re: Renewal",
        "Cảm ơn anh.\nOn Wed, Oct 1, 2026 at 10:00 AM B <b@x.example.com> wrote:\n> hi\n",
        "Cảm ơn anh.",
        ["quote"],
    ),
    (
        "crlf",
        "Re: x",
        "Thanks\r\n\r\nOn Wed, Oct 1, 2026 at 10:00 AM B <b@x.example.com> wrote:\r\n> hi\r\n",
        "Thanks",
        ["quote"],
    ),
    (
        "signature_too_long_kept",
        "Notes",
        "Nội dung.\n--\n" + "\n".join(f"dòng {i}" for i in range(20)),
        "Nội dung.\n--\n" + "\n".join(f"dòng {i}" for i in range(20)),
        [],
    ),
    ("empty", None, "", "", []),
]


@pytest.mark.parametrize(
    ("subject", "text", "expected", "kinds"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_clean_body(subject: str | None, text: str, expected: str, kinds: list[str]) -> None:
    result = clean_body(text, subject)
    assert result.body_clean == expected
    assert [c.kind for c in result.cuts] == kinds
    check_invariants(text, result)


def check_invariants(text: str, result: CleanResult) -> None:
    last = 0
    for c in result.cuts:
        assert last <= c.start < c.end <= len(text)
        last = c.end
    assert apply_cuts(text, result.cuts) == result.body_clean


def test_deterministic() -> None:
    for _, subject, text, _, _ in CASES:
        assert clean_body(text, subject) == clean_body(text, subject)


def test_cut_positions_are_character_offsets() -> None:
    text = "Cảm ơn 田中様.\n\nOn Wed, Oct 1, 2026 at 10:00 AM B <b@x.example.com> wrote:\n> 引用\n"
    result = clean_body(text, "Re: x")
    (cut,) = result.cuts
    assert text[cut.start : cut.end].startswith("On Wed")
    assert cut.end == len(text)


@pytest.mark.parametrize(
    "text",
    [
        # Review bảo mật: dòng dài sau đường kẻ làm regex địa chỉ mail chạy bậc hai.
        "hello\n━━━━━━\n" + "a" * 2_000_000,
        ("x\n━━━━━━\n" + "a" * 999 + "\n") * 2000,
        ("x\n" + "a" * 999 + ":\n") * 2000,
        "x\n" + "-" * 1000 + "\n" + ("株式会社 " + "a" * 70 + "\n") * 2000,
        "x\n" + "--\n" * 50_000,
        ("x\n━━━━━━\n" + "a:." * 333 + "\n") * 2000,
    ],
    ids=[
        "rule-then-2M-line",
        "many-rules-long-lines",
        "many-colon-lines",
        "many-company-lines",
        "many-dashdash",
        "rules-then-punctuation-lines",
    ],
)
def test_adversarial_bodies_are_fast(text: str) -> None:
    started = time.monotonic()
    clean_body(text, "Re: x")
    assert time.monotonic() - started < 2.0


def test_long_line_is_fast() -> None:
    text = "a" * 200_000 + "@" + "b" * 200_000 + ":\n> quoted\n"
    started = time.monotonic()
    result = clean_body(text, None)
    assert time.monotonic() - started < 1.0
    assert [c.kind for c in result.cuts] == ["quote"]
