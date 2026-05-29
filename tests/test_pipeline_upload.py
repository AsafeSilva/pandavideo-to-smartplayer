# tests/test_pipeline_upload.py
from pathlib import Path

import httpx
import pytest

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState
from src.pipeline import upload_one
from src.smartplayer_client import SPMedia


class FakeSP:
    """FakeSP padrão: move_media sempre falha (HTTPStatusError), poll_status chega em COMPLETED."""

    def __init__(self, move_fails_until: int = 0):
        """
        move_fails_until: número de chamadas de move_media que devem falhar antes de ter sucesso.
        0 = sempre falha (move nunca funciona, espera COMPLETED).
        """
        self.created = []
        self.upload_calls = 0
        self.poll_calls = 0
        self.move_calls = []
        self._move_fails_until = move_fails_until

    async def create_folder(self, name, parent_code=None):
        return f"sp-folder-{name[:3]}"

    async def create_media(self, name, description, external_id, total_size, public_media=True):
        self.created.append(external_id)
        return SPMedia(
            code=f"sp-media-{external_id}",
            status="DRAFT",
            urlsUpload={"urlUploadVideo": f"https://up/sp-media-{external_id}/v"},
        )

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

    async def move_media(self, folder_code, media_codes):
        self.move_calls.append((folder_code, media_codes))
        if len(self.move_calls) <= self._move_fails_until:
            response = httpx.Response(422, request=httpx.Request("PUT", "https://sp/"))
            raise httpx.HTTPStatusError("422", request=response.request, response=response)


def _make_video(tmp_path: Path, video_id: str = "v1", folder: str = "EDUCACIONAL | F") -> tuple[Manifest, Path]:
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder(folder, FolderEntry(panda_folder_id="f1", sp_folder_code="sp-folder-EDU"))
    video_path = tmp_path / "downloads" / f"{video_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"x" * 1024)
    m.upsert_video(VideoEntry(
        panda_id=video_id, panda_folder=folder, title="Aula 01",
        description="Intro", size_bytes=1024,
        state=VideoState.DOWNLOADED, local_video_path=str(video_path),
    ))
    return m, video_path


@pytest.mark.asyncio
async def test_upload_one_full_cycle(tmp_path: Path):
    """Caminho completo: move falha no poll otimista (1ª chamada), COMPLETED chega, SP_COMPLETED move → DONE."""
    m, video_path = _make_video(tmp_path)
    # move_fails_until=1: 1ª chamada (otimista no poll) falha, 2ª (bloco SP_COMPLETED) sucede
    sp = FakeSP(move_fails_until=1)

    await upload_one(sp, m, "v1", poll_interval=0, cleanup=True)

    v = m.videos["v1"]
    assert v.state == VideoState.DONE
    assert v.sp_media_code == "sp-media-v1"
    assert v.sp_embed_url == "https://player.scaleup.com.br/embed/sp-media-v1"
    assert not Path(v.local_video_path).exists()  # cleanup
    assert any(c == ("sp-folder-EDU", ["sp-media-v1"]) for c in sp.move_calls)


@pytest.mark.asyncio
async def test_early_move_liberates_worker(tmp_path: Path):
    """Move otimista OK → SP_PARTIAL, worker liberado, disco limpo, embed_url salvo."""
    m, video_path = _make_video(tmp_path)
    sp = FakeSP(move_fails_until=0)  # move sempre sucede

    await upload_one(sp, m, "v1", poll_interval=0, cleanup=True)

    v = m.videos["v1"]
    assert v.state == VideoState.SP_PARTIAL
    assert v.sp_embed_url == "https://player.scaleup.com.br/embed/sp-media-v1"
    assert not Path(v.local_video_path).exists()  # disco limpo
    assert sp.move_calls[0] == ("sp-folder-EDU", ["sp-media-v1"])
    assert sp.poll_calls == 1  # saiu após o primeiro ciclo


@pytest.mark.asyncio
async def test_move_fails_first_then_succeeds(tmp_path: Path):
    """Move falha nas primeiras N chamadas, depois funciona → SP_PARTIAL."""
    m, video_path = _make_video(tmp_path)

    class FakeSPSlowEncode(FakeSP):
        """poll_status nunca retorna COMPLETED: loop roda até move funcionar."""
        async def poll_status(self, code):
            self.poll_calls += 1
            return "COMPRESS_ENCODE"

    sp = FakeSPSlowEncode(move_fails_until=2)  # falha nas 2 primeiras tentativas de move

    await upload_one(sp, m, "v1", poll_interval=0, cleanup=True)

    v = m.videos["v1"]
    assert v.state == VideoState.SP_PARTIAL
    assert not Path(v.local_video_path).exists()
    # 3 chamadas de move: 2 falharam + 1 bem-sucedida
    assert len(sp.move_calls) == 3


@pytest.mark.asyncio
async def test_move_always_fails_timeout(tmp_path: Path):
    """Move sempre falha e poll_timeout=0 → TimeoutError."""
    m, video_path = _make_video(tmp_path)

    # poll_status nunca retorna COMPLETED, move sempre falha
    class FakeSPNeverCompletes(FakeSP):
        async def poll_status(self, code):
            self.poll_calls += 1
            return "COMPRESS_ENCODE"

    sp = FakeSPNeverCompletes(move_fails_until=999)

    with pytest.raises(TimeoutError):
        await upload_one(sp, m, "v1", poll_interval=0, poll_timeout=0, cleanup=True)


@pytest.mark.asyncio
async def test_resumes_from_sp_partial_noop(tmp_path: Path):
    """Vídeo já em SP_PARTIAL: upload_one é no-op (sem chamadas à API)."""
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder("EDUCACIONAL | F", FolderEntry(panda_folder_id="f1", sp_folder_code="sp-folder-EDU"))
    m.upsert_video(VideoEntry(
        panda_id="v1", panda_folder="EDUCACIONAL | F", title="Aula 01",
        description="", size_bytes=1024,
        state=VideoState.SP_PARTIAL,
        sp_media_code="sp-media-v1",
        sp_embed_url="https://player.scaleup.com.br/embed/sp-media-v1",
    ))

    sp = FakeSP()
    await upload_one(sp, m, "v1", poll_interval=0, cleanup=True)

    v = m.videos["v1"]
    assert v.state == VideoState.SP_PARTIAL  # estado inalterado
    assert sp.upload_calls == 0
    assert sp.poll_calls == 0
    assert sp.move_calls == []
