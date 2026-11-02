"""Client ml-service (spec 17, 18, 28) với transport giả của httpx."""

import json

import httpx
import pytest

from app.config import ml_service_url
from app.ml_client import (
    EMBED_DIM,
    RERANK_TIMEOUT_SECONDS,
    EmbeddingModelMismatch,
    HttpMlClient,
    MlUnavailable,
    vector_literal,
)


def client(handler, model: str = "BAAI/bge-m3") -> HttpMlClient:  # type: ignore[no-untyped-def]
    return HttpMlClient("http://ml.test", model=model, transport=httpx.MockTransport(handler))


def embed_handler(model: str = "BAAI/bge-m3", dim: int = EMBED_DIM):  # type: ignore[no-untyped-def]
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        calls.append(len(texts))
        return httpx.Response(
            200, json={"model": model, "dim": dim, "vectors": [[0.0] * dim for _ in texts]}
        )

    return handle, calls


async def test_embed_splits_batches() -> None:
    handle, calls = embed_handler()
    vectors = await client(handle).embed([f"t{i}" for i in range(70)])
    assert len(vectors) == 70 and calls == [32, 32, 6]


async def test_embed_model_mismatch() -> None:
    handle, _ = embed_handler(model="other/model")
    with pytest.raises(EmbeddingModelMismatch):
        await client(handle).embed(["a"])


async def test_embed_wrong_dim() -> None:
    handle, _ = embed_handler(dim=8)
    with pytest.raises(MlUnavailable):
        await client(handle).embed(["a"])


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, json={"detail": "x"}), httpx.Response(200, content=b"not json")],
)
async def test_embed_bad_response(response: httpx.Response) -> None:
    with pytest.raises(MlUnavailable):
        await client(lambda _: response).embed(["a"])


async def test_connection_error_has_no_text() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(MlUnavailable) as exc:
        await client(boom).embed(["SECRET-TEXT"])
    assert "SECRET" not in str(exc.value)


def test_vector_literal() -> None:
    assert vector_literal([0.5, -0.25, 1e-9]) == "[0.5,-0.25,1e-09]"


def test_client_no_longer_has_ner() -> None:
    """Spec 28: dịch và hỏi đáp không che nên không còn gọi /ner."""
    assert not hasattr(HttpMlClient, "ner")


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://127.0.0.1:8001", True),
        ("http://localhost:9000/", True),
        ("https://ml.internal.example.com", True),
        ("http://192.168.1.10:8001", False),
        ("ftp://127.0.0.1", False),
    ],
)
def test_ml_service_url_must_be_local_or_https(
    url: str, ok: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ML_SERVICE_URL", url)
    if ok:
        assert ml_service_url() == url.rstrip("/")
    else:
        with pytest.raises(ValueError):
            ml_service_url()


# --- rerank (spec 27 mục 7) ---


def rerank_client(scores: object, seen: list[httpx.Request] | None = None) -> HttpMlClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"model": "rr", "scores": scores})

    return client(handle)


async def test_rerank_sends_body_and_timeout() -> None:
    seen: list[httpx.Request] = []
    scores = await rerank_client([0.5, -1.0], seen).rerank("câu hỏi", ["a", "b"])
    assert scores == [0.5, -1.0]
    assert json.loads(seen[0].content) == {"query": "câu hỏi", "texts": ["a", "b"]}
    assert seen[0].url.path == "/rerank"
    assert seen[0].extensions["timeout"]["read"] == RERANK_TIMEOUT_SECONDS


async def test_rerank_truncates_to_ml_service_limits() -> None:
    seen: list[httpx.Request] = []
    await rerank_client([0.0], seen).rerank("q" * 5000, ["t" * 20_000])
    body = json.loads(seen[0].content)
    assert len(body["query"]) == 2000 and len(body["texts"][0]) == 10_000


async def test_rerank_rejects_too_many_texts_without_calling() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(MlUnavailable):
        await rerank_client([0.0] * 65, seen).rerank("q", ["t"] * 65)
    assert seen == []


@pytest.mark.parametrize(
    "scores", [[1.0], [1.0, 2.0, 3.0], "x", None, [1.0, "a"], [1.0, None], [True, 1.0]]
)
async def test_rerank_wrong_shape(scores: object) -> None:
    with pytest.raises(MlUnavailable):
        await rerank_client(scores).rerank("q", ["a", "b"])


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "1" + "0" * 400])
async def test_rerank_non_finite(bad: str) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"model":"rr","scores":[1.0,' + bad.encode() + b"]}",
            headers={"content-type": "application/json"},
        )

    with pytest.raises(MlUnavailable):
        await client(handle).rerank("q", ["a", "b"])


async def test_rerank_503_and_errors_have_no_text() -> None:
    def disabled(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "rerank_disabled"})

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("SECRET-TEXT")

    for handler in (disabled, boom):
        with pytest.raises(MlUnavailable) as exc:
            await client(handler).rerank("SECRET-Q", ["SECRET-TEXT"])
        assert "SECRET" not in str(exc.value)


def test_rerank_timeout_is_shorter_than_default() -> None:
    assert RERANK_TIMEOUT_SECONDS == 30.0
