# ruff: noqa: F811
"""Luồng dịch một mail với AI giả (spec 26 mục 6, 7, 10, 11). Dữ liệu giả.

Từ spec 28 dịch gửi text gốc (không che) và không gọi ml-service. Kiểm tra quan trọng nhất: request
chỉ có mail của chính người dùng (RLS).
"""

import re
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.client import LlmError, LlmResponse, LlmUsage
from app.ai.glossary import GlossaryMatch
from app.ai.prompts import TRANSLATE_PROMPT_VERSION, TRANSLATE_SYSTEM, TranslationOut
from app.ai.translate import (
    TranslateError,
    TranslationResult,
    check_translation,
    is_stale,
    mail_hash,
    produce_translation,
    save_translation,
)
from app.db.session import user_session
from app.processing import process_user
from tests.dbutil import add_glossary, add_project, set_ai_models
from tests.mlfake import FakeLlm
from tests.test_ai_api import api, llm, world  # noqa: F401
from tests.test_web_api import put_mail

GOOD = TranslationOut(
    subject="サンプルA gửi báo giá",
    body="Tanaka-sama, số tiền 1,250,000円.",
    notes=["Tanaka có vẻ thúc deadline"],
)


async def produce(api: AsyncEngine, llm: FakeLlm, user: int, mid: int) -> TranslationResult:
    return await produce_translation(api, llm, user, mid)


def tag(sent: str, name: str) -> str:
    """Nội dung của thẻ trong tin nhắn: lấy tới thẻ đóng cuối cùng (nội dung đã bị vô hiệu hóa)."""
    return sent.split(f"<{name}>", 1)[1].rsplit(f"</{name}>", 1)[0].strip("\n")


def user_text(llm: FakeLlm) -> str:
    return llm.requests[-1]["user"]


async def add_mail(
    owner_engine: Engine, worker: AsyncEngine, world: SimpleNamespace, mid: str, **kw: Any
) -> int:
    """Thêm một mail cho A rồi làm sạch và gom thread."""
    kw.setdefault("subject", "件名")
    kw.setdefault("day", 5)
    with owner_engine.begin() as conn:
        m = put_mail(conn, world.a, mid, [world.fa], **kw)
    await process_user(worker, world.a)
    return m


async def reply_to_est(
    owner_engine: Engine, worker: AsyncEngine, world: SimpleNamespace, mid: str, day: int, body: str
) -> int:
    return await add_mail(
        owner_engine,
        worker,
        world,
        mid,
        subject="Re: 【サンプルA】お見積りの送付",
        body=body,
        day=day,
        irt="est@a.example.jp",
        refs=("est@a.example.jp",),
    )


async def test_system_prompt_is_constant_with_rules(world: SimpleNamespace) -> None:
    assert TRANSLATE_PROMPT_VERSION == 2
    assert "Bạn là biên dịch viên Nhật–Việt và Anh–Việt" in TRANSLATE_SYSTEM
    # Không còn quy tắc về nhãn che (spec 28).
    assert "[PERSON_1]" not in TRANSLATE_SYSTEM and "nhãn" not in TRANSLATE_SYSTEM
    # Glossary thắng quy tắc tên riêng: thuật ngữ có thể là tên người, tên công ty.
    assert "không dịch nghĩa, trừ khi có trong <glossary>" in TRANSLATE_SYSTEM


async def test_translate_runs_without_ml_service(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """produce_translation không nhận ml: request có text gốc, không nhãn, không có mail của B."""
    reply = await reply_to_est(owner_engine, worker, world, "re@a.example.jp", 2, "承知しました。")
    llm.responses = [GOOD]
    await produce(api, llm, world.a, reply)
    sent = llm.sent_text()
    # Mail "est" là ngữ cảnh của reply: tên, số tiền trong đó đi nguyên văn.
    for original in ("田中", "サンプルA", "1,250,000円", "CT-2026-0042", "03-0000-0001"):
        assert original in sent, original
    assert "SECRET-OF-B" not in sent
    assert not re.search(r"\[[A-Z]+_\d+\]", sent)
    assert llm.requests[0]["system"] == TRANSLATE_SYSTEM


async def test_result_is_ai_output_as_is(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [GOOD]
    r = await produce(api, llm, world.a, world.est)
    assert (r.subject, r.body, r.notes) == (GOOD.subject, GOOD.body, GOOD.notes)
    assert r.warnings == []


async def test_label_like_text_in_mail_kept_verbatim(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Mail có sẵn chuỗi dạng nhãn: không ai thay thế gì, cả lúc gửi lẫn lúc nhận kết quả."""
    m = await add_mail(
        owner_engine,
        worker,
        world,
        "lab@a.example.jp",
        subject="件名 [ORG_9]",
        body="[PERSON_1]様 見積の件です [MONEY_1]",
    )
    llm.responses = [
        TranslationOut(
            subject="Tiêu đề [ORG_9]",
            body="[PERSON_1]-sama về báo giá [MONEY_1]",
            notes=["[ORG_3]"],
        )
    ]
    r = await produce(api, llm, world.a, m)
    assert tag(user_text(llm), "mail") == "[PERSON_1]様 見積の件です [MONEY_1]"
    assert tag(user_text(llm), "subject") == "件名 [ORG_9]"
    assert (r.subject, r.body, r.notes) == (
        "Tiêu đề [ORG_9]",
        "[PERSON_1]-sama về báo giá [MONEY_1]",
        ["[ORG_3]"],
    )
    assert r.warnings == []


async def test_result_metadata(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
) -> None:
    llm.responses = [TranslationOut(subject="s", body="b", notes=[f"n{i}" for i in range(9)])]
    r = await produce(api, llm, world.a, world.est)
    assert r.model == "test-model" and r.prompt_version == 2
    assert len(r.source_hash) == 32
    assert len(r.notes) == 5
    assert llm.requests[0]["max_tokens"] == 16_000 and llm.requests[0]["effort"] == "low"
    with owner_engine.connect() as conn:
        rows = conn.execute(text("select task, status from ai_calls")).all()
    assert [tuple(x) for x in rows] == [("translate", "ok")]


async def test_glossary_in_prompt_original_text(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "お見積り", "báo giá", note="株式会社サンプルA 向けの用語")
        add_glossary(conn, world.a, "サンプルA", "Khách A")  # tên công ty: giờ khớp được
        add_glossary(conn, world.a, "存在しない", "không có")
    llm.responses = [GOOD]
    r = await produce(api, llm, world.a, world.est)
    glossary = tag(user_text(llm), "glossary")
    assert "- お見積り = báo giá (ghi chú: 株式会社サンプルA 向けの用語)" in glossary
    assert "- サンプルA = Khách A" in glossary
    assert "存在しない" not in glossary
    assert {(g["term_norm"], g["translation"]) for g in r.glossary_used} == {
        ("お見積り", "báo giá"),
        ("サンプルa", "Khách A"),
    }
    # GOOD chứa "サンプルA" nhưng không chứa "báo giá" hay "Khách A".
    assert "glossary_missing" in r.warnings


async def test_glossary_term_that_is_a_person_name_matches(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Trước đây tên người bị che thành nhãn nên mục glossary của nó không bao giờ khớp."""
    m = await add_mail(
        owner_engine, worker, world, "n@a.example.jp", body="検収は小野寺の確認後です"
    )
    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "検収", "nghiệm thu", note="小野寺様の担当")
        add_glossary(conn, world.a, "小野寺", "Onodera")
    out = TranslationOut(subject="s", body="nghiệm thu sau khi Onodera xác nhận", notes=[])
    llm.responses = [out]
    r = await produce(api, llm, world.a, m)
    glossary = tag(user_text(llm), "glossary")
    assert "- 小野寺 = Onodera" in glossary
    assert "- 検収 = nghiệm thu (ghi chú: 小野寺様の担当)" in glossary
    assert tag(user_text(llm), "mail") == "検収は小野寺の確認後です"
    assert {g["term_norm"] for g in r.glossary_used} == {"検収", "小野寺"}
    assert "glossary_missing" not in r.warnings


async def test_conflict_sends_all_translations(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        for name, translation in (("Dự án Alpha", "báo giá"), ("Dự án Beta", "dự toán")):
            p = add_project(conn, world.a, name)
            add_glossary(conn, world.a, "お見積り", translation, project_id=p)
            conn.execute(
                text(
                    "insert into message_projects (user_id, message_id, project_id) "
                    "values (:u, :m, :p)"
                ),
                {"u": world.a, "m": world.est, "p": p},
            )
    llm.responses = [TranslationOut(subject="s", body="Gửi báo giá", notes=[])]
    r = await produce(api, llm, world.a, world.est)
    glossary = tag(user_text(llm), "glossary")
    assert "báo giá" in glossary and "dự toán" in glossary and "chọn theo ngữ cảnh" in glossary
    assert "glossary_conflict" in r.warnings and "glossary_missing" not in r.warnings
    assert r.glossary_used == [
        {"term_norm": "お見積り", "translation": "báo giá"},
        {"term_norm": "お見積り", "translation": "dự toán"},
    ]


async def test_body_truncated_at_paragraph(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    para = "あ" * 3249
    body = "\n\n".join([para] * 4)
    assert len(body) > 13_000
    m = await add_mail(owner_engine, worker, world, "long@a.example.jp", body=body)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, llm, world.a, m)
    sent = tag(user_text(llm), "mail")
    assert len(sent) <= 12_000
    assert sent == "\n\n".join([para] * 3)
    assert "truncated" in r.warnings


async def test_long_paragraph_cut_keeps_limit(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    m = await add_mail(owner_engine, worker, world, "one@a.example.jp", body="あ" * 13_000)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, llm, world.a, m)
    assert tag(user_text(llm), "mail") == "あ" * 12_000
    assert "truncated" in r.warnings


async def test_subject_and_context_cut_to_limits(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    await reply_to_est(owner_engine, worker, world, "p1@a.example.jp", 2, "い" * 1_600)
    m = await add_mail(
        owner_engine,
        worker,
        world,
        "p2@a.example.jp",
        subject="Re: 【サンプルA】お見積りの送付" + "件" * 400,
        body="本文",
        day=3,
        irt="est@a.example.jp",
        refs=("est@a.example.jp",),
    )
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, llm, world.a, m)
    sent = user_text(llm)
    assert tag(sent, "context") == "い" * 1_500
    assert len(tag(sent, "subject")) == 300
    assert "truncated" not in r.warnings  # chỉ thân mail báo truncated


async def test_body_over_limit_is_cut_and_hash_uses_full_text(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    body = "\n\n".join(["あ" * 4_000] * 12)
    assert len(body) > 45_000
    m = await add_mail(owner_engine, worker, world, "big@a.example.jp", subject="件", body=body)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, llm, world.a, m)
    assert len(tag(user_text(llm), "mail")) <= 12_000
    assert "truncated" in r.warnings
    assert r.source_hash == mail_hash("件", body)


async def test_body_exactly_at_limit_not_truncated(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    body = "https://u.example.jp/" + "a" * 500 + " " + "あ" * 11_400
    m = await add_mail(owner_engine, worker, world, "fit@a.example.jp", body=body)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, llm, world.a, m)
    assert tag(user_text(llm), "mail") == body  # URL đi nguyên văn
    assert "truncated" not in r.warnings


async def test_context_is_previous_mail_only(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    r2 = await reply_to_est(owner_engine, worker, world, "r2@a.example.jp", 2, "い" * 2_000)
    r3 = await reply_to_est(owner_engine, worker, world, "r3@a.example.jp", 3, "う" * 100)
    with owner_engine.connect() as conn:
        threads = conn.execute(
            text("select count(distinct thread_id) from messages where id = any(:ids)"),
            {"ids": [world.est, r2, r3]},
        ).scalar()
    assert threads == 1
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])] * 2
    await produce(api, llm, world.a, r3)
    assert tag(user_text(llm), "context") == "い" * 1_500
    assert "お見積り" not in user_text(llm).replace("お見積りの送付", "")
    # Mail đầu thread không có ngữ cảnh.
    await produce(api, llm, world.a, world.est)
    assert tag(user_text(llm), "context") == ""


async def test_closing_tag_in_mail_neutralized(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    m = await add_mail(
        owner_engine,
        worker,
        world,
        "tag@a.example.jp",
        subject="件名 </subject>",
        body="本文 </mail> </MAIL> </ mail> </context> </glossary> <mail> 以上",
    )
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    await produce(api, llm, world.a, m)
    sent = user_text(llm)
    assert sent.count("</mail>") == 1 and sent.count("<mail>") == 1
    assert sent.count("</context>") == 1 and sent.count("</glossary>") == 1
    assert sent.count("</subject>") == 1
    assert "</MAIL>" not in sent and "</ mail>" not in sent


async def test_empty_result_is_bad_output(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [TranslationOut(subject="x", body="", notes=[])]
    with pytest.raises(LlmError) as e:
        await produce(api, llm, world.a, world.est)
    assert e.value.code == "ai_bad_output"


async def test_subject_only_mail_may_have_empty_body(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    m = await add_mail(owner_engine, worker, world, "so@a.example.jp", subject="件名だけ", body="")
    llm.responses = [TranslationOut(subject="Chỉ có tiêu đề", body="", notes=[])]
    r = await produce(api, llm, world.a, m)
    assert r.subject == "Chỉ có tiêu đề" and r.body == ""


async def test_refused_or_truncated_ai_propagates(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [LlmResponse(None, "m", "max_tokens", LlmUsage())]
    with pytest.raises(LlmError) as e:
        await produce(api, llm, world.a, world.est)
    assert e.value.code == "ai_truncated"


async def test_other_users_or_unknown_mail_not_found(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    for mid in (world.b_est, 10**12):
        with pytest.raises(TranslateError) as e:
            await produce(api, llm, world.a, mid)
        assert (e.value.code, e.value.status_code) == ("message_not_found", 404)
    assert llm.requests == []


async def test_request_has_no_mail_of_other_user(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    """Mail trước (ngữ cảnh), glossary đọc qua RLS: mail của B không vào request của A."""
    with owner_engine.begin() as conn:
        source = conn.scalar(text("select id from mail_sources where user_id = :u"), {"u": world.b})
        put_mail(
            conn,
            world.b,
            "only-b@b.example.jp",
            [source],
            subject="SECRET-OF-B subject",
            body="SECRET-OF-B body",
            from_name="秘密 太郎",
            frm="secret@b-only.example.jp",
        )
        add_glossary(conn, world.b, "お見積り", "SECRET-OF-B")
    llm.responses = [GOOD]
    await produce(api, llm, world.a, world.est)
    sent = llm.sent_text()
    assert "SECRET-OF-B" not in sent and "秘密" not in sent and "b-only" not in sent


async def test_nul_from_ai_is_dropped(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [TranslationOut(subject="a\x00b", body="c\x00d", notes=["e\x00f"])]
    r = await produce(api, llm, world.a, world.est)
    assert (r.subject, r.body, r.notes) == ("ab", "cd", ["ef"])


async def test_not_ready_and_empty(
    api: AsyncEngine,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    empty = await add_mail(owner_engine, worker, world, "e@a.example.jp", subject="", body="")
    with owner_engine.begin() as conn:
        raw = put_mail(conn, world.a, "raw@a.example.jp", [world.fa], subject="s", body="b")
    with pytest.raises(TranslateError) as e:
        await produce(api, llm, world.a, raw)
    assert (e.value.code, e.value.status_code) == ("mail_not_ready", 409)
    with pytest.raises(TranslateError) as e:
        await produce(api, llm, world.a, empty)
    assert (e.value.code, e.value.status_code) == ("nothing_to_translate", 400)
    assert llm.requests == []


async def test_uses_own_model_and_requires_it(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    """Job gọi thẳng produce_translation, không qua kiểm tra của route."""
    with owner_engine.begin() as conn:
        conn.execute(text("delete from user_ai_settings where user_id = :u"), {"u": world.a})
    with pytest.raises(LlmError) as e:
        await produce(api, llm, world.a, world.est)
    assert e.value.code == "ai_model_missing"
    assert llm.requests == []
    with owner_engine.begin() as conn:
        set_ai_models(conn, world.a, translate="m1")
    llm.responses = [GOOD]
    r = await produce(api, llm, world.a, world.est)
    assert llm.requests[0]["model"] == "m1" and r.model == "m1"
    assert "fallbacks" not in llm.requests[0]


async def test_no_connection_held_while_waiting_for_ai(
    api: AsyncEngine, world: SimpleNamespace
) -> None:
    held: list[int] = []

    class Probe(FakeLlm):
        async def create(self, **kwargs: Any) -> Any:
            held.append(api.sync_engine.pool.checkedout())  # type: ignore[attr-defined]
            return await super().create(**kwargs)

    probe = Probe(responses=[GOOD])
    await produce(api, probe, world.a, world.est)
    assert held == [0]


async def test_save_translation_upserts(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [GOOD, TranslationOut(subject="s2", body="b2", notes=[])]
    first = await produce(api, llm, world.a, world.est)
    second = await produce(api, llm, world.a, world.est)
    for r in (first, second):
        async with user_session(api, world.a) as s:
            await save_translation(s, world.a, world.est, r)
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select translated_subject, notes, warnings, glossary_used, model, source_hash "
                "from message_translations"
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].translated_subject == "s2" and rows[0].notes == []
    assert bytes(rows[0].source_hash) == second.source_hash


async def test_save_after_mail_deleted_is_not_found(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [GOOD]
    r = await produce(api, llm, world.a, world.est)
    with owner_engine.begin() as conn:
        conn.execute(text("delete from messages where id = :m"), {"m": world.est})
    with pytest.raises(TranslateError) as e:
        async with user_session(api, world.a) as s:
            await save_translation(s, world.a, world.est, r)
    assert e.value.code == "message_not_found"


def match(term: str, *translations: str, conflict: bool = False) -> GlossaryMatch:
    return GlossaryMatch(term, term, tuple(translations), None, conflict)


def test_check_translation_codes() -> None:
    missing = match("見積", "báo giá")
    assert check_translation("bản dịch", [missing], truncated=True) == [
        "glossary_missing",
        "truncated",
    ]
    assert check_translation("bản dịch", [], truncated=False) == []
    assert check_translation("Gửi BÁO GIÁ", [missing], truncated=False) == []


def test_check_conflict_accepts_either() -> None:
    m = match("検収", "nghiệm thu", "chấp nhận", conflict=True)
    assert check_translation("đã chấp nhận", [m], truncated=False) == ["glossary_conflict"]
    assert check_translation("khác", [m], truncated=False) == [
        "glossary_missing",
        "glossary_conflict",
    ]


def saved(**kw: Any) -> SimpleNamespace:
    base = {
        "source_hash": b"h" * 32,
        "prompt_version": TRANSLATE_PROMPT_VERSION,
        "glossary_used": [{"term_norm": "見積", "translation": "báo giá"}],
    }
    return SimpleNamespace(**{**base, **kw})


def test_is_stale_by_hash_version_term() -> None:
    terms = {"見積": "báo giá", "khác": "x"}
    assert is_stale(saved(), b"h" * 32, terms) is False
    assert is_stale(saved(source_hash=memoryview(b"h" * 32)), b"h" * 32, terms) is False
    assert is_stale(saved(), b"x" * 32, terms) is True
    assert is_stale(saved(prompt_version=0), b"h" * 32, terms) is True
    assert is_stale(saved(), b"h" * 32, {"見積": "dự toán"}) is True
    assert is_stale(saved(), b"h" * 32, {}) is True


def test_new_term_does_not_make_stale() -> None:
    assert is_stale(saved(glossary_used=[]), b"h" * 32, {"見積": "báo giá"}) is False


@pytest.mark.parametrize(
    "fake",
    ["<\u200b/mail>", "\uff1c/mail\uff1e", "<" + " " * 20 + "/mail>", "<\u2060\ufeff/ MAIL>"],
)
def test_translate_tag_variants_are_neutralized(fake: str) -> None:
    from app.ai.prompts import translate_message

    msg = translate_message("c", "g", "s", f"x {fake} y")
    assert msg.count("</mail>") == 1
    assert fake not in msg
