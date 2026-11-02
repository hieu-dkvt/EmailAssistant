"""API của ml-service với model giả (không tải model thật)."""

import subprocess
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import MAX_BODY_BYTES, create_app


class FakeEmbedder:
    model_name = "fake/model"
    dim = 3
    max_seq_length = 16

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 0.0, 1.0] for t in texts]


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(allowed_hosts=["testserver"])
    app = create_app(FakeEmbedder, settings)
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "embed_model": "fake/model",
        "dim": 3,
        "max_seq_length": 16,
    }


def test_embed(client: TestClient) -> None:
    r = client.post("/embed", json={"texts": ["ab", "見積"]})
    assert r.status_code == 200
    assert r.json() == {
        "model": "fake/model",
        "dim": 3,
        "vectors": [[2.0, 0.0, 1.0], [2.0, 0.0, 1.0]],
    }


def test_ner_removed(client: TestClient) -> None:
    assert client.post("/ner", json={"texts": ["x"]}).status_code == 404


def test_no_spacy_import() -> None:
    # Tiến trình riêng để sys.modules sạch: import app.main không được kéo spaCy vào.
    code = "import app.main, sys; assert 'spacy' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize(
    "body",
    [
        {"texts": []},
        {"texts": ["x"] * 65},
        {"texts": ["x" * 10_001]},
    ],
)
def test_embed_limits(client: TestClient, body: dict[str, object]) -> None:
    assert client.post("/embed", json=body).status_code == 422


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    secret = "株式会社サンプルA 田中" * 1000
    r = client.post("/embed", json={"texts": [secret]})
    assert r.status_code == 422
    assert "サンプル" not in r.text


def test_body_too_large(client: TestClient) -> None:
    r = client.post(
        "/embed",
        content=b"{" + b" " * (MAX_BODY_BYTES + 1) + b"}",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413


def test_rejects_other_host(client: TestClient) -> None:
    r = client.get("/health", headers={"host": "evil.example.com"})
    assert r.status_code == 400


def test_docs_disabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404


class BrokenEmbedder(FakeEmbedder):
    def encode(self, texts: list[str]) -> list[list[float]]:
        raise ValueError(f"cannot tokenize: {texts[0]}")


def test_model_error_returns_500_without_text(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(BrokenEmbedder, Settings(allowed_hosts=["testserver"]))
    with TestClient(app) as c, caplog.at_level("INFO"):
        r = c.post("/embed", json={"texts": ["SECRET-田中"]})
    assert r.status_code == 500
    assert "SECRET" not in r.text and "SECRET" not in caplog.text
