"""python -m app.run: phát hiện model đã có trong cache (để tự chạy offline)."""

from pathlib import Path

import pytest

from app.run import model_in_cache


def test_model_in_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    assert not model_in_cache("BAAI/bge-m3")
    snap = tmp_path / "hub" / "models--BAAI--bge-m3" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    assert model_in_cache("BAAI/bge-m3")
    assert not model_in_cache("other/model")
