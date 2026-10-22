"""Gom thread (spec 2026-10-17-threading-design.md). Dữ liệu tự dựng, tên miền *.example.*"""

import base64
import contextlib
import importlib.util
import io
import json
import mailbox
import random
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

import pytest

from app.mail.hashing import normalize_message_id
from app.mail.threading import (
    MailRow,
    Override,
    ThreadingResult,
    assign_thread_ids,
    build_threads,
    thread_index_key,
)

GENERATOR = Path(__file__).resolve().parents[2] / "data" / "generate_sample_profile.py"
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def row(
    id: int,
    mid: str | None = None,
    *,
    refs: tuple[str, ...] = (),
    irt: str | None = None,
    subject: str | None = "Báo giá",
    frm: str | None = "client@a.example.jp",
    to: tuple[str, ...] = ("pm@vn.example.com",),
    day: float | None = 0,
    ti: str | None = None,
) -> MailRow:
    return MailRow(
        id=id,
        message_id=mid if mid is not None else f"m{id}@x.example",
        in_reply_to=irt,
        references=refs,
        thread_index=ti,
        subject=subject,
        from_addr=frm,
        recipients=frozenset(to),
        sent_at=None if day is None else T0 + timedelta(days=day),
    )


def groups_of(result: ThreadingResult) -> set[frozenset[int]]:
    return {frozenset(g) for g in result.groups}


# --- golden: profile tự sinh bằng data/generate_sample_profile.py (vào thư mục tạm) ---


@pytest.fixture(scope="module")
def sample_profile(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, list[str]]]:
    out = tmp_path_factory.mktemp("sample")
    spec = importlib.util.spec_from_file_location("sample_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    gen: Any = importlib.util.module_from_spec(spec)  # script ngoài package: không có kiểu
    spec.loader.exec_module(gen)
    gen.ROOT = out
    gen.PROFILE = out / "thunderbird-profile"
    gen.IMAP = gen.PROFILE / "ImapMail" / "imap.vn-outsource.example.com"
    gen.LOCAL = gen.PROFILE / "Mail" / "Local Folders"
    with contextlib.redirect_stdout(io.StringIO()):
        gen.main()
    expected = json.loads((out / "expected_threads.json").read_text(encoding="utf-8"))
    return gen.PROFILE, expected


def _decode(value: str | None) -> str | None:
    return None if value is None else str(make_header(decode_header(value)))


def load_rows(profile: Path) -> list[MailRow]:
    """Đọc mbox như collector + ingest: một bản mỗi Message-ID, địa chỉ viết thường."""
    rows: dict[str, MailRow] = {}
    for path in sorted(profile.rglob("*")):
        if not path.is_file() or path.suffix or path.stat().st_size == 0:
            continue
        for msg in mailbox.mbox(path, create=False):
            mid = normalize_message_id(msg["Message-ID"])
            assert mid is not None
            if mid in rows:
                continue
            refs = (normalize_message_id(r) for r in (msg["References"] or "").split())
            recipients = getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))
            rows[mid] = MailRow(
                id=len(rows) + 1,
                message_id=mid,
                in_reply_to=normalize_message_id(msg["In-Reply-To"]),
                references=tuple(r for r in refs if r),
                thread_index=msg["Thread-Index"],
                subject=_decode(msg["Subject"]),
                from_addr=parseaddr(msg["From"])[1].lower(),
                recipients=frozenset(a.lower() for _, a in recipients if a),
                sent_at=parsedate_to_datetime(msg["Date"]),
            )
    return list(rows.values())


def test_golden_sample_profile(sample_profile: tuple[Path, dict[str, list[str]]]) -> None:
    profile, expected = sample_profile
    rows = load_rows(profile)
    by_mid = {r.message_id: r.id for r in rows}
    result = build_threads(rows)

    want = {
        frozenset(by_mid[normalize_message_id(m) or ""] for m in mids) for mids in expected.values()
    }
    assert sum(len(g) for g in want) == len(rows)
    assert groups_of(result) == want

    # A-bug mail 7: Outlook làm mất header, chỉ nối được bằng subject (lớp 3).
    (a_bug_7,) = [r.id for r in rows if (r.message_id or "").startswith("a-bug.7.")]
    assert result.method[a_bug_7] == "subject"
    # Mail "Re:" lẻ không có thread gốc vẫn đứng riêng.
    (s7,) = [r.id for r in rows if (r.message_id or "").startswith("s7.")]
    assert result.method[s7] == "root"


def test_input_order_does_not_matter(sample_profile: tuple[Path, dict[str, list[str]]]) -> None:
    rows = load_rows(sample_profile[0])
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    a, b = build_threads(rows), build_threads(shuffled)
    assert (a.groups, a.parent, a.method) == (b.groups, b.parent, b.method)


# --- lớp 1 ---


def test_reply_chain_and_parents() -> None:
    rows = [
        row(1, "a@x"),
        row(2, "b@x", refs=("a@x",), irt="a@x", day=1),
        row(3, "c@x", refs=("a@x", "b@x"), irt="b@x", day=2),
    ]
    r = build_threads(rows)
    assert r.groups == [[1, 2, 3]]
    assert r.parent == {1: None, 2: 1, 3: 2}
    assert r.method == {1: "root", 2: "references", 3: "references"}


def test_siblings_of_missing_parent_are_grouped() -> None:
    rows = [
        row(1, "b@x", refs=("missing@x",), day=1),
        row(2, "c@x", refs=("missing@x",), day=2),
    ]
    r = build_threads(rows)
    assert r.groups == [[1, 2]]
    assert r.parent == {1: None, 2: None}


def test_parent_skips_phantom_container() -> None:
    rows = [row(1, "a@x"), row(2, "c@x", refs=("a@x", "missing@x"), day=2)]
    r = build_threads(rows)
    assert r.groups == [[1, 2]]
    assert r.parent[2] == 1


def test_in_reply_to_wins_when_references_incomplete() -> None:
    rows = [
        row(1, "a@x"),
        row(2, "b@x", refs=("a@x",), day=1),
        row(3, "c@x", refs=("a@x",), irt="b@x", day=2),
    ]
    r = build_threads(rows)
    assert r.parent[3] == 2


def test_reference_loop_terminates() -> None:
    rows = [row(1, "a@x", refs=("b@x",)), row(2, "b@x", refs=("a@x",), day=1)]
    r = build_threads(rows)
    assert r.groups == [[1, 2]]
    assert None in (r.parent[1], r.parent[2])


def test_message_ids_compare_case_insensitively() -> None:
    rows = [row(1, "ABC@X.example"), row(2, "b@x", irt="abc@x.EXAMPLE", day=1)]
    assert build_threads(rows).groups == [[1, 2]]


def test_messages_without_message_id_stay_apart() -> None:
    rows = [row(1, ""), row(2, "", day=1)]
    rows = [MailRow(**{**r.__dict__, "message_id": None}) for r in rows]
    assert build_threads(rows).groups == [[1], [2]]


# --- lớp 2 ---


def thread_index(conversation: bytes, child: bytes = b"") -> str:
    return base64.b64encode(conversation + child).decode()


def test_thread_index_joins_threads_without_references() -> None:
    conv = bytes(range(22))
    rows = [
        row(1, subject="Kickoff", ti=thread_index(conv)),
        row(2, subject="Lịch họp", day=1, ti=thread_index(conv, b"\x01\x02\x03\x04\x05")),
    ]
    r = build_threads(rows)
    assert r.groups == [[1, 2]]
    assert r.method[2] == "thread_index"


@pytest.mark.parametrize("value", ["AQHa0011223344556677", "not base64 !!", "", None])
def test_thread_index_invalid_or_short_is_ignored(value: str | None) -> None:
    assert thread_index_key(value) is None
    rows = [row(1, subject="A", ti=value), row(2, subject="B", day=1, ti=value)]
    assert build_threads(rows).groups == [[1], [2]]


# --- lớp 3 ---


def test_subject_joins_reply_without_headers() -> None:
    rows = [
        row(1, subject="Báo giá", frm="client@a.example.jp", to=("pm@vn.example.com",)),
        row(2, subject="RE: Báo giá", frm="pm@vn.example.com", to=("client@a.example.jp",), day=2),
    ]
    r = build_threads(rows)
    assert r.groups == [[1, 2]]
    assert r.method[2] == "subject"
    assert r.parent[2] is None


@pytest.mark.parametrize(
    "reply",
    [
        row(2, subject="Báo giá", frm="pm@vn.example.com", to=("client@a.example.jp",), day=2),
        row(2, subject="Re: Báo giá", frm="other@b.example.jp", to=("pm@vn.example.com",), day=2),
        row(2, subject="Re: Báo giá", frm="pm@vn.example.com", to=("other@b.example.jp",), day=2),
        row(2, subject="Re: Báo giá", frm="pm@vn.example.com", to=("client@a.example.jp",), day=61),
        row(
            2, subject="Re: Báo giá 2", frm="pm@vn.example.com", to=("client@a.example.jp",), day=2
        ),
        row(2, subject="Re: Báo giá", frm="pm@vn.example.com", to=("client@a.example.jp",), day=-1),
        row(2, subject="Re: Báo giá", frm="pm@vn.example.com", day=None),
    ],
    ids=[
        "no-prefix",
        "sender-not-participant",
        "no-recipient-participant",
        "after-60-days",
        "other-subject",
        "before-thread",
        "no-date",
    ],
)
def test_subject_does_not_join(reply: MailRow) -> None:
    rows = [row(1, subject="Báo giá", frm="client@a.example.jp", to=("pm@vn.example.com",)), reply]
    assert groups_of(build_threads(rows)) == {frozenset({1}), frozenset({2})}


def test_subject_picks_most_recent_thread() -> None:
    rows = [
        row(1, "a@x", subject="Định kỳ", day=0),
        row(2, "b@x", subject="Định kỳ", day=10),
        row(3, "c@x", subject="Re: Định kỳ", irt="b@x", day=11),
        row(
            4,
            "d@x",
            subject="Re: Định kỳ",
            frm="pm@vn.example.com",
            to=("client@a.example.jp",),
            day=12,
        ),
    ]
    assert groups_of(build_threads(rows)) == {frozenset({1}), frozenset({2, 3, 4})}


def test_subject_matches_any_message_of_thread() -> None:
    rows = [
        row(1, "a@x", subject="Báo giá"),
        row(2, "b@x", subject="Re: Báo giá (bổ sung)", irt="a@x", day=1),
        row(
            3,
            "c@x",
            subject="Re: Báo giá (bổ sung)",
            frm="pm@vn.example.com",
            to=("client@a.example.jp",),
            day=2,
        ),
    ]
    assert build_threads(rows).groups == [[1, 2, 3]]


# --- sửa tay ---


def test_split_detaches_subtree_and_is_not_rejoined() -> None:
    conv = bytes(range(22))
    rows = [
        row(1, "a@x", subject="Báo giá", ti=thread_index(conv)),
        row(2, "b@x", subject="Re: Báo giá", refs=("a@x",), day=1, ti=thread_index(conv, b"1")),
        row(
            3,
            "c@x",
            subject="Re: Báo giá",
            refs=("a@x", "b@x"),
            day=2,
            frm="pm@vn.example.com",
            to=("client@a.example.jp",),
        ),
    ]
    r = build_threads(rows, [Override("split", 2)])
    assert groups_of(r) == {frozenset({1}), frozenset({2, 3})}
    assert r.parent == {1: None, 2: None, 3: 2}
    assert r.method[2] == "root"


def test_merge_joins_two_threads() -> None:
    rows = [row(1, "a@x", subject="Một"), row(2, "b@x", subject="Hai", day=3)]
    r = build_threads(rows, [Override("merge", 2, 1)])
    assert r.groups == [[1, 2]]
    assert r.method[2] == "merge"


def test_merge_with_unknown_message_is_ignored() -> None:
    rows = [row(1, "a@x"), row(2, "b@x", subject="Khác", day=1)]
    assert build_threads(rows, [Override("merge", 2, 99)]).groups == [[1], [2]]


# --- giữ id thread ---


def test_assign_thread_ids_keeps_ids() -> None:
    groups = [[1, 2, 3, 4], [5], [6, 7]]
    old = {1: 10, 2: 10, 3: 11, 4: 11, 5: None, 6: 11, 7: 12}
    # Nhóm 1 gồm thread 10 (2 mail) và 11 (2 mail): bằng nhau → id nhỏ (10). Nhóm 3: 11 còn trống.
    assert assign_thread_ids(groups, old) == [10, None, 11]


def test_assign_thread_ids_larger_overlap_wins() -> None:
    groups = [[1], [2, 3, 4]]
    old = {1: 10, 2: 10, 3: 10, 4: 11}
    assert assign_thread_ids(groups, old) == [None, 10]
