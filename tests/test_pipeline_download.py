# tests/test_pipeline_download.py
import asyncio
from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import VideoEntry, VideoState
from src.pipeline import download_one


class FakePanda:
    def __init__(self):
        self.requested = []
        self.poll_calls = 0

    async def request_download(self, video_id, quality, title):
        self.requested.append((video_id, quality, title))

    async def poll_download(self, video_id, quality="original", language="pt-BR"):
        self.poll_calls += 1
        if self.poll_calls < 2:
            return None
        return f"https://signed/{video_id}.mp4"

    async def download_file(self, url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"video-bytes")
        return 11


@pytest.mark.asyncio
async def test_download_one_full_cycle(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T1"))

    panda = FakePanda()
    download_dir = tmp_path / "downloads"
    await download_one(panda, m, "v1", download_dir, quality="original", poll_interval=0)

    v = m.videos["v1"]
    assert v.state == VideoState.DOWNLOADED
    assert v.local_video_path is not None
    assert Path(v.local_video_path).exists()
    assert panda.requested == [("v1", "original", "T1")]
