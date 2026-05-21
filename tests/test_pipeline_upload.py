# tests/test_pipeline_upload.py
from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState
from src.pipeline import upload_one


class FakeSP:
    def __init__(self):
        self.created = []
        self.upload_calls = 0
        self.poll_calls = 0

    async def create_folder(self, name, parent_code=None):
        return f"sp-folder-{name[:3]}"

    async def create_media(self, name, description, external_id, total_size, public_media=True):
        self.created.append(external_id)
        return f"sp-media-{external_id}"

    async def get_upload_urls(self, code):
        return {
            "urlUploadVideo": f"https://up/{code}/v",
            "urlUploadPoster": f"https://up/{code}/p",
        }

    async def upload_binary(self, url, file_path, content_type):
        self.upload_calls += 1

    async def poll_status(self, code):
        self.poll_calls += 1
        return "COMPLETED" if self.poll_calls >= 2 else "COMPRESS_ENCODE"


@pytest.mark.asyncio
async def test_upload_one_full_cycle(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    # pasta já mapeada
    m.upsert_folder("EDUCACIONAL | F", FolderEntry(panda_folder_id="f1", sp_folder_code="sp-folder-EDU"))
    # vídeo pronto para upload
    video_path = tmp_path / "downloads" / "v1.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"x" * 1024)
    m.upsert_video(VideoEntry(
        panda_id="v1", panda_folder="EDUCACIONAL | F", title="Aula 01",
        description="Intro", size_bytes=1024,
        state=VideoState.DOWNLOADED, local_video_path=str(video_path),
    ))

    sp = FakeSP()
    await upload_one(sp, m, "v1", poll_interval=0, cleanup=True)

    v = m.videos["v1"]
    assert v.state == VideoState.DONE
    assert v.sp_media_code == "sp-media-v1"
    assert v.sp_embed_url == "https://player.scaleup.com.br/embed/sp-media-v1"
    assert not Path(v.local_video_path).exists()  # cleanup
