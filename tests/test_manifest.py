from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import VideoEntry, VideoState


def test_load_nonexistent_returns_empty(tmp_path: Path):
    m = Manifest.load(tmp_path / "manifest.json")
    assert m.folders == {}
    assert m.videos == {}


def test_save_then_load_roundtrip(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="T1"))
    m.save()
    m2 = Manifest.load(path)
    assert "v1" in m2.videos
    assert m2.videos["v1"].title == "T1"
    assert m2.videos["v1"].state == VideoState.PENDING


def test_save_is_atomic(tmp_path: Path, monkeypatch):
    """Se o write falhar na metade, o arquivo original permanece intacto."""
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="ok"))
    m.save()

    # Substitui video por novo (mas vamos sabotar o save)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="novo"))

    # Sabota o os.replace para simular falha pós-write
    import src.manifest as mod
    def boom(*args, **kwargs):
        raise OSError("disco cheio")
    monkeypatch.setattr(mod.os, "replace", boom)

    with pytest.raises(OSError):
        m.save()

    # arquivo original ainda válido com título "ok"
    m_orig = Manifest.load(path)
    assert m_orig.videos["v1"].title == "ok"
