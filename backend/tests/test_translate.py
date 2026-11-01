# ruff: noqa: F811
"""Luồng dịch một mail với AI giả (spec 26 mục 6, 7, 10, 11). Dữ liệu giả.

Kiểm tra quan trọng nhất (NDA): mọi text gửi AI đã che, che lỗi thì không gọi AI.
"""

import itertools
import re
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.ask import build_masker
from app.ai.client import LlmError, LlmResponse, LlmUsage
from app.ai.glossary import GlossaryMatch
from app.ai.masking import cut_masked
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
from app.ml_client import MlUnavailable, NerEntity
from app.processing import process_user
from tests.dbutil import add_glossary, add_project
from tests.mlfake import FakeLlm, FakeMl
from tests.test_ai_api import SECRETS, api, fresh_people_cache, llm, ml, world  # noqa: F401
from tests.test_web_api import put_mail

GOOD = TranslationOut(
    subject="[ORG_1] gửi báo giá",
    body="[PERSON_1]-sama, số tiền [MONEY_1].",
    notes=["[PERSON_1] có vẻ thúc deadline"],
)


async def produce(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, user: int, mid: int, *, custom: bool = False
) -> TranslationResult:
    return await produce_translation(api, ml, llm, user, mid, custom_provider=custom)


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
    assert TRANSLATE_PROMPT_VERSION == 1
    assert "Bạn là biên dịch viên Nhật–Việt và Anh–Việt" in TRANSLATE_SYSTEM
    assert "Giữ nguyên từng nhãn dạng [PERSON_1], [ORG_2], [MONEY_1]." in TRANSLATE_SYSTEM


async def test_sends_only_masked_text(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    reply = await reply_to_est(owner_engine, worker, world, "re@a.example.jp", 2, "承知しました。")
    llm.responses = [GOOD]
    await produce(api, ml, llm, world.a, reply)
    sent = llm.sent_text()
    # Mail "est" là ngữ cảnh của reply: tên, số tiền trong đó cũng phải che.
    for secret in SECRETS:
        assert secret not in sent, secret
    assert "SECRET-OF-B" not in sent
    assert "[MONEY_1]" in sent and "[PERSON_1]" in sent
    assert llm.requests[0]["system"] == TRANSLATE_SYSTEM


async def test_result_unmasked_in_all_three_fields(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [GOOD]
    r = await produce(api, ml, llm, world.a, world.est)
    assert "サンプルA" in r.subject and "[ORG_1]" not in r.subject
    assert "1,250,000円" in r.body and "[PERSON_1]" not in r.body and "[MONEY_1]" not in r.body
    assert len(r.notes) == 1 and "[PERSON_1]" not in r.notes[0]


async def test_result_metadata(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
) -> None:
    llm.responses = [
        TranslationOut(subject="[ORG_1]", body="[MONEY_1]", notes=[f"n{i}" for i in range(9)])
    ]
    r = await produce(api, ml, llm, world.a, world.est)
    assert r.model == "claude-sonnet-5-5" and r.prompt_version == 1
    assert len(r.source_hash) == 32
    assert len(r.notes) == 5
    assert llm.requests[0]["max_tokens"] == 16_000 and llm.requests[0]["effort"] == "low"
    with owner_engine.connect() as conn:
        rows = conn.execute(text("select task, status from ai_calls")).all()
    assert [tuple(x) for x in rows] == [("translate", "ok")]


async def test_glossary_in_prompt_masked(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "お見積り", "báo giá", note="株式会社サンプルA 向けの用語")
        add_glossary(conn, world.a, "サンプルA", "Khách A")  # đã bị che trong mail
    llm.responses = [GOOD]
    r = await produce(api, ml, llm, world.a, world.est)
    glossary = tag(user_text(llm), "glossary")
    assert "báo giá" in glossary and "お見積り" in glossary
    assert "サンプルA" not in llm.sent_text()
    assert "Khách A" not in glossary
    assert r.glossary_used == [{"term_norm": "お見積り", "translation": "báo giá"}]


async def test_new_name_in_glossary_note_also_masks_mail(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Tên chỉ NER/mẫu nhận ra trong note: mail phải được che lại với từ điển đã mở rộng."""
    m = await add_mail(
        owner_engine, worker, world, "n@a.example.jp", body="検収は小野寺の確認後です"
    )
    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "検収", "nghiệm thu", note="小野寺様の担当")
        add_glossary(conn, world.a, "小野寺", "Onodera")
    llm.responses = [TranslationOut(subject="s", body="nghiệm thu", notes=[])]
    r = await produce(api, ml, llm, world.a, m)
    sent = llm.sent_text()
    assert "小野寺" not in sent
    # Mục của tên vừa học không còn khớp: bản dịch của nó không được gửi đi kèm nhãn.
    assert "Onodera" not in sent
    assert "[PERSON_" in tag(user_text(llm), "mail")
    assert r.glossary_used == [{"term_norm": "検収", "translation": "nghiệm thu"}]
    assert "glossary_missing" not in r.warnings


async def test_slot_freed_by_masked_term_cannot_leak_next_name(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Mục bị loại nhường chỗ trong giới hạn 60 cho mục thứ 61, mục này lại dạy thêm một tên."""
    fillers = [f"項目{i:02d}" for i in range(58)]
    body = " ".join(["検収作業完了", "小野寺の確認", *fillers, "項目99", "五十嵐"])
    m = await add_mail(owner_engine, worker, world, "slot@a.example.jp", body=body)
    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "検収作業完了", "xong nghiệm thu", note="小野寺様の担当")
        add_glossary(conn, world.a, "小野寺の確認", "Onodera xác nhận")
        for term in fillers:
            add_glossary(conn, world.a, term, "mục")
        add_glossary(conn, world.a, "項目99", "mục cuối", note="五十嵐様の件")
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    await produce(api, ml, llm, world.a, m)
    sent = llm.sent_text()
    assert "小野寺" not in sent and "五十嵐" not in sent and "Onodera" not in sent
    assert "mục cuối" in tag(user_text(llm), "glossary")


async def test_mask_not_settling_sends_nothing(
    api: AsyncEngine, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    note = "いろはにほへとちりぬるをわかよたれそつねならむ"
    calls = itertools.count()

    class Drifting(FakeMl):
        """Mỗi lần che glossary lại nhận ra một tên mới: từ điển không bao giờ ổn định."""

        async def ner(self, texts: list[str]) -> list[list[NerEntity]]:
            found = await super().ner(texts)
            for i, t in enumerate(texts):
                if t == note:
                    at = next(calls) * 2
                    found[i] = [NerEntity(at, at + 2, "PERSON")]
            return found

    with owner_engine.begin() as conn:
        add_glossary(conn, world.a, "お見積り", "báo giá", note=note)
    with pytest.raises(TranslateError) as e:
        await produce(api, Drifting(), llm, world.a, world.est)
    assert e.value.code == "mask_unstable"
    assert llm.requests == []


async def test_conflict_sends_all_translations(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
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
    r = await produce(api, ml, llm, world.a, world.est)
    glossary = tag(user_text(llm), "glossary")
    assert "báo giá" in glossary and "dự toán" in glossary and "chọn theo ngữ cảnh" in glossary
    assert "Alpha" not in llm.sent_text() and "Beta" not in llm.sent_text()
    assert "glossary_conflict" in r.warnings and "glossary_missing" not in r.warnings
    assert r.glossary_used == [
        {"term_norm": "お見積り", "translation": "báo giá"},
        {"term_norm": "お見積り", "translation": "dự toán"},
    ]


async def test_ner_down_no_call(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    ml.ner_down = True
    with pytest.raises(MlUnavailable):
        await produce(api, ml, llm, world.a, world.est)
    assert llm.requests == []


async def test_body_truncated_at_paragraph(
    api: AsyncEngine,
    ml: FakeMl,
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
    r = await produce(api, ml, llm, world.a, m)
    sent = tag(user_text(llm), "mail")
    assert len(sent) <= 12_000
    assert sent == "\n\n".join([para] * 3)
    assert "truncated" in r.warnings


async def test_long_paragraph_cut_keeps_limit(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    m = await add_mail(owner_engine, worker, world, "one@a.example.jp", body="あ" * 13_000)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, ml, llm, world.a, m)
    assert tag(user_text(llm), "mail") == "あ" * 12_000
    assert "truncated" in r.warnings


# Không có khoảng trắng gần đó, có dấu ngoặc để mẫu EMAIL, PHONE khớp khi còn nguyên.
SECRET_TAIL = "(partial-local-part@nowhere.example.jp,090-1234-5678) 以上です"


def assert_no_fragment(sent: str) -> None:
    """Không có mảnh địa chỉ, số điện thoại, và không có nhãn cắt dở ở cuối."""
    for fragment in ("partial", "nowhere", "example.jp", "090", "1234"):
        assert fragment not in sent, fragment
    assert not re.search(r"\[[A-Z]{0,8}(?:_\d{0,6})?$", sent)


@pytest.mark.parametrize("before_cut", [5, 20, 30, 45, 50])
async def test_cut_after_masking_never_sends_fragment(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
    before_cut: int,
) -> None:
    """Vị trí 12.000 của text gốc rơi vào giữa địa chỉ hay số điện thoại: cắt trước khi che thì
    mảnh còn lại không khớp mẫu che và bị gửi đi. Cắt sau khi che thì không còn mảnh."""
    body = "あ" * (12_000 - before_cut) + SECRET_TAIL
    m = await add_mail(owner_engine, worker, world, "cut@a.example.jp", body=body)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    await produce(api, ml, llm, world.a, m)
    sent = tag(user_text(llm), "mail")
    assert len(sent) <= 12_000
    assert_no_fragment(sent)


async def test_cut_never_inside_label(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    body = "あ" * 11_997 + " 090-1234-5678 " + "い" * 500
    m = await add_mail(owner_engine, worker, world, "lab@a.example.jp", body=body)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    await produce(api, ml, llm, world.a, m)
    sent = tag(user_text(llm), "mail")
    assert sent.rstrip() == "あ" * 11_997
    assert "[" not in sent


def test_cut_masked_rules() -> None:
    assert cut_masked("abc", 10, capped=False) == ("abc", False)
    # Ranh giới đoạn trước, kể cả khi có cuối dòng gần giới hạn hơn; không có thì cuối dòng.
    text = "a" * 60 + "\n\n" + "b" * 30 + "\n" + "c" * 30
    assert cut_masked(text, 100, capped=False) == ("a" * 60, True)
    assert cut_masked("a" * 60 + "\n" + "b" * 60, 100, capped=False) == ("a" * 60, True)
    # Ranh giới trong nửa đầu giới hạn: bỏ qua, không bỏ mất phần lớn mail.
    assert cut_masked("a\n\n" + "b" * 200, 100, capped=False) == ("a\n\n" + "b" * 97, True)
    # Không cắt trong nhãn, kể cả nhãn bắt đầu ngay trước giới hạn.
    assert cut_masked("x" * 95 + "[PERSON_12] y", 100, capped=False) == ("x" * 95, True)
    assert cut_masked("x" * 90 + "[ORG_1]" + "y" * 10, 100, capped=False)[1] is True
    assert cut_masked("[CONTRACT_1]" * 3, 20, capped=False) == ("[CONTRACT_1]", True)


async def test_subject_and_context_cut_after_masking(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    previous = "い" * 1_490 + SECRET_TAIL
    await reply_to_est(owner_engine, worker, world, "p1@a.example.jp", 2, previous)
    m = await add_mail(
        owner_engine,
        worker,
        world,
        "p2@a.example.jp",
        subject="Re: 【サンプルA】お見積りの送付" + "件" * 270 + SECRET_TAIL,
        body="本文",
        day=3,
        irt="est@a.example.jp",
        refs=("est@a.example.jp",),
    )
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    await produce(api, ml, llm, world.a, m)
    sent = user_text(llm)
    assert len(tag(sent, "context")) <= 1_500 and len(tag(sent, "subject")) <= 300
    assert_no_fragment(tag(sent, "context"))
    assert_no_fragment(tag(sent, "subject"))


async def test_body_over_mask_limit_is_capped_and_hash_uses_full_text(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    body = "\n\n".join(["あ" * 4_000] * 12)
    assert len(body) > 45_000
    m = await add_mail(owner_engine, worker, world, "big@a.example.jp", subject="件", body=body)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, ml, llm, world.a, m)
    assert max(len(t) for t in ml.ner_inputs) <= 40_000
    assert "truncated" in r.warnings
    assert r.source_hash == mail_hash("件", body)


async def test_fragment_at_mask_limit_not_sent_when_text_shrinks(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Giới hạn 40.000 cũng có thể cắt đôi một địa chỉ. Bình thường phần đó bị cắt 12.000 bỏ đi,
    nhưng URL dài co lại thành nhãn thì đuôi text đã che lọt vào phần gửi."""
    head = "普通の本文です。\n" * 200
    urls = ("https://u.example.jp/" + "a" * 479 + " ") * 75
    filler = "あ" * (39_990 - len(head) - len(urls))
    m = await add_mail(
        owner_engine, worker, world, "s@a.example.jp", body=head + urls + filler + SECRET_TAIL
    )
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, ml, llm, world.a, m)
    sent = tag(user_text(llm), "mail")
    assert len(sent) < 12_000
    assert_no_fragment(sent)
    assert "truncated" in r.warnings


async def test_url_growing_on_uppercase_does_not_break_flow(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    url = "https://a.jp/" + "ΐ" * 340
    m = await add_mail(owner_engine, worker, world, "up@a.example.jp", body=f"詳細 {url} です")
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    await produce(api, ml, llm, world.a, m)
    assert tag(user_text(llm), "mail") == "詳細 [URL_1] です"


async def test_short_mail_not_cut_when_masked_text_fits(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """truncated nói về phần gửi đi: text gốc hơn 12.000 nhưng che xong còn đủ thì không cắt."""
    body = "https://u.example.jp/" + "a" * 500 + " " + "あ" * 11_500
    m = await add_mail(owner_engine, worker, world, "fit@a.example.jp", body=body)
    llm.responses = [TranslationOut(subject="s", body="b", notes=[])]
    r = await produce(api, ml, llm, world.a, m)
    assert tag(user_text(llm), "mail") == "[URL_1] " + "あ" * 11_500
    assert "truncated" not in r.warnings


async def test_context_is_previous_mail_only(
    api: AsyncEngine,
    ml: FakeMl,
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
    await produce(api, ml, llm, world.a, r3)
    assert tag(user_text(llm), "context") == "い" * 1_500
    assert "お見積り" not in user_text(llm).replace("お見積りの送付", "")
    # Mail đầu thread không có ngữ cảnh.
    await produce(api, ml, llm, world.a, world.est)
    assert tag(user_text(llm), "context") == ""


async def test_closing_tag_in_mail_neutralized(
    api: AsyncEngine,
    ml: FakeMl,
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
    await produce(api, ml, llm, world.a, m)
    sent = user_text(llm)
    assert sent.count("</mail>") == 1 and sent.count("<mail>") == 1
    assert sent.count("</context>") == 1 and sent.count("</glossary>") == 1
    assert sent.count("</subject>") == 1
    assert "</MAIL>" not in sent and "</ mail>" not in sent


async def test_empty_result_is_bad_output(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [TranslationOut(subject="x", body="", notes=[])]
    with pytest.raises(LlmError) as e:
        await produce(api, ml, llm, world.a, world.est)
    assert e.value.code == "ai_bad_output"


async def test_subject_only_mail_may_have_empty_body(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    m = await add_mail(owner_engine, worker, world, "so@a.example.jp", subject="件名だけ", body="")
    llm.responses = [TranslationOut(subject="Chỉ có tiêu đề", body="", notes=[])]
    r = await produce(api, ml, llm, world.a, m)
    assert r.subject == "Chỉ có tiêu đề" and r.body == ""


async def test_refused_or_truncated_ai_propagates(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [LlmResponse(None, "m", "max_tokens", LlmUsage())]
    with pytest.raises(LlmError) as e:
        await produce(api, ml, llm, world.a, world.est)
    assert e.value.code == "ai_truncated"


async def test_other_users_or_unknown_mail_not_found(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    for mid in (world.b_est, 10**12):
        with pytest.raises(TranslateError) as e:
            await produce(api, ml, llm, world.a, mid)
        assert (e.value.code, e.value.status_code) == ("message_not_found", 404)
    assert llm.requests == []


async def test_masker_dictionary_is_own_mail_only(
    api: AsyncEngine, world: SimpleNamespace, owner_engine: Engine
) -> None:
    """build_masker dựa vào RLS: tên chỉ có trong mail của B không vào từ điển của A."""
    with owner_engine.begin() as conn:
        source = conn.scalar(text("select id from mail_sources where user_id = :u"), {"u": world.b})
        put_mail(
            conn,
            world.b,
            "only-b@b.example.jp",
            [source],
            subject="s",
            body="b",
            from_name="秘密 太郎",
            frm="secret@b-only.example.jp",
        )
    async with user_session(api, world.a) as s:
        masker = await build_masker(s, FakeMl(), world.a, [])
    assert await masker.mask(["秘密 太郎"]) == ["秘密 太郎"]


async def test_nul_from_ai_is_dropped(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace
) -> None:
    llm.responses = [TranslationOut(subject="a\x00b", body="c\x00d", notes=["e\x00f"])]
    r = await produce(api, ml, llm, world.a, world.est)
    assert (r.subject, r.body, r.notes) == ("ab", "cd", ["ef"])


async def test_not_ready_and_empty(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    empty = await add_mail(owner_engine, worker, world, "e@a.example.jp", subject="", body="")
    with owner_engine.begin() as conn:
        raw = put_mail(conn, world.a, "raw@a.example.jp", [world.fa], subject="s", body="b")
    with pytest.raises(TranslateError) as e:
        await produce(api, ml, llm, world.a, raw)
    assert (e.value.code, e.value.status_code) == ("mail_not_ready", 409)
    with pytest.raises(TranslateError) as e:
        await produce(api, ml, llm, world.a, empty)
    assert (e.value.code, e.value.status_code) == ("nothing_to_translate", 400)
    assert llm.requests == []


async def test_custom_provider_uses_own_model_and_requires_it(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    """Job gọi thẳng produce_translation, không qua kiểm tra của route."""
    with pytest.raises(LlmError) as e:
        await produce(api, ml, llm, world.a, world.est, custom=True)
    assert e.value.code == "ai_model_missing"
    assert llm.requests == []
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "insert into user_ai_settings (user_id, task, model) values (:u, 'translate', 'm1')"
            ),
            {"u": world.a},
        )
    llm.responses = [GOOD]
    r = await produce(api, ml, llm, world.a, world.est, custom=True)
    assert llm.requests[0]["model"] == "m1" and r.model == "m1"
    assert llm.requests[0]["fallbacks"] is False


async def test_no_connection_held_while_waiting_for_ai(
    api: AsyncEngine, ml: FakeMl, world: SimpleNamespace
) -> None:
    held: list[int] = []

    class Probe(FakeLlm):
        async def create(self, **kwargs: Any) -> Any:
            held.append(api.sync_engine.pool.checkedout())  # type: ignore[attr-defined]
            return await super().create(**kwargs)

    probe = Probe(responses=[GOOD])
    await produce(api, ml, probe, world.a, world.est)
    assert held == [0]


async def test_save_translation_upserts(
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [GOOD, TranslationOut(subject="s2", body="b2", notes=[])]
    first = await produce(api, ml, llm, world.a, world.est)
    second = await produce(api, ml, llm, world.a, world.est)
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
    api: AsyncEngine, ml: FakeMl, llm: FakeLlm, world: SimpleNamespace, owner_engine: Engine
) -> None:
    llm.responses = [GOOD]
    r = await produce(api, ml, llm, world.a, world.est)
    with owner_engine.begin() as conn:
        conn.execute(text("delete from messages where id = :m"), {"m": world.est})
    with pytest.raises(TranslateError) as e:
        async with user_session(api, world.a) as s:
            await save_translation(s, world.a, world.est, r)
    assert e.value.code == "message_not_found"


def match(term: str, *translations: str, conflict: bool = False) -> GlossaryMatch:
    return GlossaryMatch(term, term, tuple(translations), None, conflict)


def test_check_labels_missing_unknown() -> None:
    assert "labels_missing" in check_translation(
        ["[PERSON_1]", "[ORG_1] [ORG_1]"], ["[PERSON_1]"], "x", [], truncated=False
    )
    assert "labels_unknown" in check_translation(
        ["[PERSON_1]"], ["[PERSON_1] [PERSON_2]"], "x", [], truncated=False
    )
    # So theo tập hợp, không theo số lần.
    assert check_translation(["[ORG_1] [ORG_1]"], ["[ORG_1]"], "x", [], truncated=False) == []


def test_labels_missing_ignores_notes() -> None:
    warnings = check_translation(
        ["[PERSON_1]"], ["x"], "x", [], truncated=False, masked_notes=["[PERSON_1]"]
    )
    assert warnings == ["labels_missing"]


def test_labels_unknown_means_not_in_anything_sent() -> None:
    # Có trong ngữ cảnh hoặc glossary: đã gửi đi, không phải nhãn AI tự nghĩ ra.
    assert (
        check_translation(
            ["a"], ["[ORG_1]"], "x", [], truncated=False, other_sent=["<ctx> [ORG_1]"]
        )
        == []
    )
    # Nhãn lạ ở notes cũng tính: thay ngược không được, người dùng thấy nguyên nhãn.
    assert check_translation(["a"], ["b"], "x", [], truncated=False, masked_notes=["[ORG_9]"]) == [
        "labels_unknown"
    ]


async def test_labels_compare_with_everything_sent(
    api: AsyncEngine,
    ml: FakeMl,
    llm: FakeLlm,
    world: SimpleNamespace,
    owner_engine: Engine,
    worker: AsyncEngine,
) -> None:
    """Luồng thật: [MONEY_1] chỉ có trong ngữ cảnh, nhãn có trong mail mà chỉ xuất hiện ở notes."""
    reply = await reply_to_est(
        owner_engine, worker, world, "lb@a.example.jp", 2, "承知しました。田中様"
    )
    llm.responses = [TranslationOut(subject="[ORG_1]", body="[MONEY_1]", notes=["[PERSON_1]"])]
    r = await produce(api, ml, llm, world.a, reply)
    assert "labels_unknown" not in r.warnings
    assert "labels_missing" in r.warnings


def test_check_glossary_missing() -> None:
    assert (
        check_translation(["a"], ["b"], "Gửi BÁO GIÁ", [match("見積", "báo giá")], truncated=False)
        == []
    )
    assert check_translation(
        ["a"], ["b"], "Gửi hóa đơn", [match("見積", "báo giá")], truncated=False
    ) == ["glossary_missing"]


def test_check_conflict_accepts_either() -> None:
    m = match("検収", "nghiệm thu", "chấp nhận", conflict=True)
    assert check_translation(["a"], ["b"], "đã chấp nhận", [m], truncated=False) == [
        "glossary_conflict"
    ]
    assert check_translation(["a"], ["b"], "khác", [m], truncated=False) == [
        "glossary_missing",
        "glossary_conflict",
    ]


def test_check_truncated() -> None:
    assert check_translation(["a"], ["b"], "c", [], truncated=True) == ["truncated"]


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
