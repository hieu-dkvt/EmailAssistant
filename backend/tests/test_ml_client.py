"""Client ml-service (spec 17, 18, 28) với transport giả của httpx."""

import json

import httpx
import pytest

from app.config import ml_service_url
from app.ml_client import (
    EMBED_DIM,
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
