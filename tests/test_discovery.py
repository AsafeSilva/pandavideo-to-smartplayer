# tests/test_discovery.py
from pathlib import Path

import pytest

from src.discovery import discover
from src.manifest import Manifest
from src.models import VideoState
from src.panda_client import PandaFolder, PandaVideo


class FakePandaClient:
    def __init__(self):
        self.folders = [
            PandaFolder(id="f1", name="EDUCACIONAL | LIVES", parent_folder_id=None),
            PandaFolder(id="f2", name="EDUCACIONAL | Curso", parent_folder_id=None),
            PandaFolder(id="f3", name="AREA COMERCIAL", parent_folder_id=None),
        ]
        self.videos = {
            "f1": [PandaVideo(id="v1", title="Aula 01", folder_id="f1", length=120,
                              size=10_000_000, thumbnail="https://t/v1.jpg",
                              status="converted")],
            "f2": [PandaVideo(id="v2", title="Aula 02", folder_id="f2", length=300,
                              size=20_000_000, thumbnail="https://t/v2.jpg",
                              status="converted"),
                   PandaVideo(id="v3", title="Aula 03", folder_id="f2", length=400,
                              size=30_000_000, thumbnail="https://t/v3.jpg",
                              status="converting")],
            "f3": [PandaVideo(id="v4", title="Comercial", folder_id="f3", length=60,
                              size=5_000_000, thumbnail="https://t/v4.jpg",
                              status="converted")],
        }

    async def list_folders(self, parent_folder_id=None):
        if parent_folder_id is not None:
            return []  # sem subpastas no fake
        return self.folders

    async def list_videos(self, folder_id, limit=100):
        return self.videos.get(folder_id, [])


@pytest.mark.asyncio
async def test_discovery_filters_by_prefix(tmp_path: Path):
    panda = FakePandaClient()
    m = Manifest.load(tmp_path / "m.json")
    await discover(panda, m, prefix="EDUCACIONAL |")

    # 3 vídeos das pastas EDUCACIONAL, mas v3 (converting) é pulado
    assert {"v1", "v2"} == set(m.videos.keys())
    assert "EDUCACIONAL | LIVES" in m.folders
    assert "EDUCACIONAL | Curso" in m.folders
    assert "AREA COMERCIAL" not in m.folders


@pytest.mark.asyncio
async def test_discovery_persists_metadata(tmp_path: Path):
    panda = FakePandaClient()
    m = Manifest.load(tmp_path / "m.json")
    await discover(panda, m, prefix="EDUCACIONAL |")

    v1 = m.videos["v1"]
    assert v1.title == "Aula 01"
    assert v1.duration_sec == 120
    assert v1.size_bytes == 10_000_000
    assert v1.thumbnail_url == "https://t/v1.jpg"
    assert v1.state == VideoState.PENDING


@pytest.mark.asyncio
async def test_discovery_preserves_sp_folder_code(tmp_path: Path):
    """Segunda chamada a discover() não deve sobrescrever sp_folder_code já mapeado."""
    from src.models import FolderEntry
    panda = FakePandaClient()
    m = Manifest.load(tmp_path / "m.json")
    await discover(panda, m, prefix="EDUCACIONAL |")

    # Simula que run_pipeline preencheu o sp_folder_code
    m.upsert_folder(
        "EDUCACIONAL | LIVES",
        FolderEntry(panda_folder_id="f1", sp_folder_code="sp-xyz"),
    )
    m.save()

    # Segunda chamada ao discover — não deve sobrescrever
    await discover(panda, m, prefix="EDUCACIONAL |")

    assert m.folders["EDUCACIONAL | LIVES"].sp_folder_code == "sp-xyz"
