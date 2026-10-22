"""Embedding mail (spec 18): cắt đoạn, worker ghi message_chunks, RLS."""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import embedding
from app.embedding import CHUNK_CHARS, CHUNK_OVERLAP, MAX_CHUNKS, split_chunks
from app.ml_client import EmbeddingModelMismatch, MlUnavailable
from app.processing import process_user
from tests.dbutil import add_mail, add_user, as_user
from tests.mlfake import FakeMl

# --- cắt đoạn ---


def test_short_body_one_chunk() -> None:
    assert [(c.no, c.start, c.end) for c in split_chunks("短い本文")] == [(0, 0, 4)]
    assert [(c.no, c.start, c.end) for c in split_chunks("")] == [(0, 0, 0)]


def test_long_body_cut_at_line_with_overlap() -> None:
    body = "".join(f"{i:03d} " + "あ" * 95 + "\n" for i in range(30))  # 100 ký tự / dòng
    chunks = split_chunks(body)
    assert chunks[0].start == 0
    for c in chunks:
        assert c.end - c.start <= CHUNK_CHARS
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.start == a.end - CHUNK_OVERLAP  # đoạn sau lặp lại phần cuối đoạn trước
        assert body[a.end - 1] == "\n"  # cắt ở cuối dòng
    assert chunks[-1].end == len(body)


def test_long_line_hard_cut_and_sentence_cut() -> None:
    body = "x" * 2000
    chunks = split_chunks(body)
    assert [c.end - c.start for c in chunks[:2]] == [CHUNK_CHARS, CHUNK_CHARS]
    sentence = ("これはテストの文です。" * 100)[:1500]
    c0 = split_chunks(sentence)[0]
    assert sentence[c0.end - 1] == "。"


def test_max_chunks() -> None:
    assert len(split_chunks("y" * 100_000)) == MAX_CHUNKS


# --- DB ---


def chunk_rows(engine: Engine, user_id: int) -> list[tuple[int, int, int, int]]:
    with engine.connect() as conn:
        return [
            (r.message_id, r.chunk_no, r.start_pos, r.end_pos)
            for r in conn.execute(
                text(
                    "select message_id, chunk_no, start_pos, end_pos from message_chunks "
                    "where user_id = :u order by message_id, chunk_no"
                ),
                {"u": user_id},
            )
        ]


@pytest.fixture
async def mails(owner_engine: Engine, worker: AsyncEngine, clean: None) -> dict[str, int]:
    with owner_engine.begin() as conn:
        a = add_user(conn, "a@example.com")
        b = add_user(conn, "b@example.com")
        ids = {
            "a_old": add_mail(conn, a, "old@x.example", subject="古い見積", day=0),
            "a_new": add_mail(conn, a, "new@x.example", subject="障害報告", day=5),
            "a_long": add_mail(conn, a, "long@x.example", body="仕様の説明です。\n" * 200, day=3),
            "b_mail": add_mail(conn, b, "b@x.example", subject="見積", day=1),
        }
    for u in (a, b):
        await process_user(worker, u)  # làm sạch + gom thread
    return {**ids, "a": a, "b": b}


async def test_embed_newest_first_and_idempotent(
    mails: dict[str, int], worker: AsyncEngine, owner_engine: Engine
) -> None:
    ml = FakeMl()
    first = await embedding.embed_batch(worker, ml, mails["a"], 1)
    assert (first.messages, first.more) == (1, True)
    assert {r[0] for r in chunk_rows(owner_engine, mails["a"])} == {mails["a_new"]}

    stats = await embedding.embed_user(worker, ml, mails["a"])
    assert stats.messages == 2 and not stats.more
    rows = chunk_rows(owner_engine, mails["a"])
    assert {r[0] for r in rows} == {mails["a_old"], mails["a_new"], mails["a_long"]}
    assert len([r for r in rows if r[0] == mails["a_long"]]) > 1
    assert await embedding.count_pending(worker, mails["a"]) == 0
    assert await embedding.pending_users(worker) == [mails["b"]]  # chỉ mail của A được embed

    again = await embedding.embed_user(worker, ml, mails["a"])
    assert again.messages == 0
    assert chunk_rows(owner_engine, mails["a"]) == rows


async def test_reembed_after_recleaning(
    mails: dict[str, int], worker: AsyncEngine, owner_engine: Engine
) -> None:
    ml = FakeMl()
    await embedding.embed_user(worker, ml, mails["a"])
    with owner_engine.begin() as conn:
        conn.execute(
            text("update messages set cleaner_version = cleaner_version + 1 where id = :m"),
            {"m": mails["a_old"]},
        )
    assert await embedding.count_pending(worker, mails["a"]) == 1
    ml.embedded.clear()
    stats = await embedding.embed_user(worker, ml, mails["a"])
    assert stats.messages == 1 and len(ml.embedded) == 1
    with owner_engine.connect() as conn:
        n = conn.scalar(
            text("select count(*) from message_chunks where message_id = :m"),
            {"m": mails["a_old"]},
        )
    assert n == 1


async def test_unclean_mail_not_embedded(owner_engine: Engine, worker: AsyncEngine, clean: None):
    with owner_engine.begin() as conn:
        u = add_user(conn, "a@example.com")
        add_mail(conn, u, "raw@x.example")
    assert await embedding.pending_users(worker) == []
    assert (await embedding.embed_user(worker, FakeMl(), u)).messages == 0


async def test_model_mismatch_and_unavailable_write_nothing(
    mails: dict[str, int], worker: AsyncEngine, owner_engine: Engine
) -> None:
    with pytest.raises(EmbeddingModelMismatch):
        await embedding.embed_user(worker, FakeMl(model="other/model"), mails["a"])
    with pytest.raises(MlUnavailable):
        await embedding.embed_user(worker, FakeMl(down=True), mails["a"])
    assert chunk_rows(owner_engine, mails["a"]) == []


async def test_rls_and_cascade(
    mails: dict[str, int], worker: AsyncEngine, owner_engine: Engine, api_engine: Engine
) -> None:
    ml = FakeMl()
    for u in (mails["a"], mails["b"]):
        await embedding.embed_user(worker, ml, u)
    with api_engine.begin() as conn:
        as_user(conn, mails["a"])
        seen = {r[0] for r in conn.execute(text("select distinct user_id from message_chunks"))}
    assert seen == {mails["a"]}
    with api_engine.begin() as conn:
        as_user(conn, mails["a"])
        with pytest.raises(DBAPIError):
            conn.execute(text("delete from message_chunks"))
    # Xóa mail (như xóa mail của folder, chạy bằng app_api) xóa luôn đoạn và vector.
    with api_engine.begin() as conn:
        as_user(conn, mails["a"])
        conn.execute(text("delete from messages where id = :m"), {"m": mails["a_new"]})
    assert mails["a_new"] not in {r[0] for r in chunk_rows(owner_engine, mails["a"])}
