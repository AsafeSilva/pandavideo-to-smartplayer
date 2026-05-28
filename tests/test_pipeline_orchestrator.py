# tests/test_pipeline_orchestrator.py
from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState
from src.pipeline import run_pipeline
from src.smartplayer_client import SPMedia


class FakePanda:
    async def get_video(self, video_id):
        class _V:
            video_external_id = video_id
        return _V()

    async def request_download(self, video_id, quality, title):
        pass

    async def poll_download(self, video_id, quality="original", language="pt-BR"):
        return f"https://signed/{video_id}.mp4"

    async def download_file(self, url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"x" * 100)
        return 100


class FakeSP:
    async def create_folder(self, name, parent_code=None):
        return f"sp-{name[:3]}"

    async def create_media(self, name, description, external_id, total_size, public_media=True):
        return SPMedia(
            code=f"m-{external_id}",
            status="DRAFT",
            urlsUpload={"urlUploadVideo": f"https://up/m-{external_id}/v"},
        )

    async def get_upload_urls(self, code):
        return {"urlUploadVideo": "https://up/v", "urlUploadPoster": "https://up/p"}

    async def upload_binary(self, url, file_path, content_type):
        pass

    async def poll_status(self, code):
        return "COMPLETED"

    async def move_media(self, folder_code, media_codes):
        pass


@pytest.mark.asyncio
async def test_run_pipeline_completes_all_pending(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder("F1", FolderEntry(panda_folder_id="f1"))
    for i in range(5):
        m.upsert_video(VideoEntry(
            panda_id=f"v{i}", panda_folder="F1", title=f"T{i}", size_bytes=100,
        ))
    m.save()

    await run_pipeline(
        panda=FakePanda(),
        sp=FakeSP(),
        manifest=m,
        download_dir=tmp_path / "downloads",
        max_download_concurrency=2,
        max_upload_concurrency=2,
        poll_interval=0,
    )

    for i in range(5):
        v = m.videos[f"v{i}"]
        assert v.state == VideoState.DONE
        assert v.sp_embed_url is not None


def test_disk_used_gb_soma_mp4s(tmp_path: Path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "a.mp4").write_bytes(b"x" * 500)
    (dl / "b.mp4").write_bytes(b"x" * 500)
    (dl / "c.txt").write_bytes(b"x" * 9999)  # não deve ser contado

    from src.pipeline import _disk_used_gb
    result = _disk_used_gb(dl)

    assert abs(result - 1000 / (1024 ** 3)) < 1e-12


def test_disk_used_gb_pasta_vazia(tmp_path: Path):
    dl = tmp_path / "downloads"
    dl.mkdir()

    from src.pipeline import _disk_used_gb
    assert _disk_used_gb(dl) == 0.0


def test_disk_used_gb_pasta_inexistente(tmp_path: Path):
    from src.pipeline import _disk_used_gb
    # glob numa pasta inexistente não lança exceção — retorna 0
    assert _disk_used_gb(tmp_path / "nao_existe") == 0.0
