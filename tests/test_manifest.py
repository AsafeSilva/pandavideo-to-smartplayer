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


def test_save_retries_permission_error_on_replace(tmp_path: Path, monkeypatch):
    """WinError 5 no os.replace (antivírus/indexador com o destino aberto) é
    transitório — o save deve reintentar em vez de derrubar o vídeo em migração."""
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="T1"))

    import src.manifest as mod
    real_replace = mod.os.replace
    calls = {"n": 0}

    def flaky(src_, dst_, *a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "Acesso negado")
        return real_replace(src_, dst_, *a, **kw)

    monkeypatch.setattr(mod.os, "replace", flaky)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    m.save()

    assert calls["n"] == 3
    assert Manifest.load(path).videos["v1"].title == "T1"


def test_save_reraises_permission_error_after_attempts(tmp_path: Path, monkeypatch):
    """Se nunca liberar, o erro sobe — não engolir falha de persistência."""
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="T1"))

    import src.manifest as mod
    monkeypatch.setattr(
        mod.os, "replace",
        lambda *a, **kw: (_ for _ in ()).throw(PermissionError(13, "Acesso negado")),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    with pytest.raises(PermissionError):
        m.save()
    assert not (tmp_path / "manifest.json.tmp").exists()


def test_transition_updates_state_and_persists(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T"))
    m.transition("v1", VideoState.DOWNLOADED, local_video_path="data/downloads/v1.mp4")

    reloaded = Manifest.load(path)
    assert reloaded.videos["v1"].state == VideoState.DOWNLOADED
    assert reloaded.videos["v1"].local_video_path == "data/downloads/v1.mp4"


def test_videos_in_state_filters(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T1"))
    m.upsert_video(VideoEntry(panda_id="v2", panda_folder="F", title="T2", state=VideoState.DOWNLOADED))
    m.upsert_video(VideoEntry(panda_id="v3", panda_folder="F", title="T3", state=VideoState.DONE))

    pending = m.videos_in_state(VideoState.PENDING)
    assert {v.panda_id for v in pending} == {"v1"}

    in_flight = m.videos_in_state(VideoState.DOWNLOADED, VideoState.SP_PROCESSING)
    assert {v.panda_id for v in in_flight} == {"v2"}


def test_mark_failed_increments_retry(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T"))
    m.mark_failed("v1", "timeout on download")
    m.mark_failed("v1", "timeout again")

    v = m.videos["v1"]
    assert v.state == VideoState.FAILED
    assert v.retry_count == 2
    assert "timeout again" in v.last_error


def test_transition_rejects_invalid_field(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T"))
    with pytest.raises(ValueError, match="VideoEntry has no field"):
        m.transition("v1", VideoState.DOWNLOADED, nonexistent_field="oops")
