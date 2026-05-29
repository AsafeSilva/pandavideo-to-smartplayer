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


class FakePandaDiskCheck(FakePanda):
    """FakePanda que verifica que o disco está abaixo do limite no início de cada download."""
    def __init__(self, download_dir: Path, limit_gb: float):
        self._dl_dir = download_dir
        self._limit_gb = limit_gb

    async def download_file(self, url, dest):
        from src.pipeline import _disk_used_gb
        used = _disk_used_gb(self._dl_dir)
        assert used < self._limit_gb, (
            f"download iniciou com disco acima do limite: {used:.2e} GB >= {self._limit_gb:.2e} GB"
        )
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


@pytest.mark.asyncio
async def test_disk_backpressure(tmp_path: Path, monkeypatch):
    """Verifica que downloads pausam quando disco cheio e retomam após upload liberar espaço."""
    monkeypatch.setenv("RETRY_FAST", "1")

    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder("F1", FolderEntry(panda_folder_id="f1"))
    for i in range(2):
        m.upsert_video(VideoEntry(
            panda_id=f"dv{i}", panda_folder="F1", title=f"DT{i}", size_bytes=100,
        ))
    m.save()

    # FakePanda.download_file escreve 100 bytes por vídeo.
    # Limite = 50 bytes → após o 1º download (100 bytes no disco), o 2º deve esperar
    # até que o upload do 1º delete o arquivo.
    limit_gb = 50 / (1024 ** 3)

    await run_pipeline(
        panda=FakePandaDiskCheck(tmp_path / "downloads", limit_gb),
        sp=FakeSP(),
        manifest=m,
        download_dir=tmp_path / "downloads",
        max_download_concurrency=1,
        max_upload_concurrency=1,
        poll_interval=0,
        max_disk_gb=limit_gb,
    )

    for i in range(2):
        assert m.videos[f"dv{i}"].state == VideoState.DONE, f"dv{i} não chegou a DONE"
    # Confirma que nenhum mp4 sobrou no disco
    assert list((tmp_path / "downloads").glob("*.mp4")) == []


@pytest.mark.asyncio
async def test_finalizer_marks_done_after_partial(tmp_path: Path):
    """Pipeline com early-move: finalizer processa SP_PARTIAL e chega em DONE."""

    class FakeSPEarlyMove(FakeSP):
        """move_media sempre sucede (early-move), poll_status retorna COMPLETED na 2ª chamada por vídeo."""

        def __init__(self):
            self._poll_calls: dict = {}
            self.move_calls = []

        async def poll_status(self, code):
            n = self._poll_calls.get(code, 0) + 1
            self._poll_calls[code] = n
            # 1ª chamada: COMPRESS_ENCODE → early move; 2ª chamada (pelo finalizer): COMPLETED
            return "COMPLETED" if n >= 2 else "COMPRESS_ENCODE"

        async def move_media(self, folder_code, media_codes):
            self.move_calls.append((folder_code, media_codes))
            # Não lança: early-move ativo, worker liberado

    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder("F1", FolderEntry(panda_folder_id="f1"))
    for i in range(3):
        m.upsert_video(VideoEntry(
            panda_id=f"v{i}", panda_folder="F1", title=f"T{i}", size_bytes=100,
        ))
    m.save()

    sp = FakeSPEarlyMove()
    await run_pipeline(
        panda=FakePanda(),
        sp=sp,
        manifest=m,
        download_dir=tmp_path / "downloads",
        max_download_concurrency=2,
        max_upload_concurrency=2,
        poll_interval=0,
        finalizer_interval=0,
    )

    for i in range(3):
        assert m.videos[f"v{i}"].state == VideoState.DONE, f"v{i} não chegou a DONE"


@pytest.mark.asyncio
async def test_resume_from_sp_partial_on_restart(tmp_path: Path):
    """Manifest com vídeo em SP_PARTIAL: run_pipeline finaliza sem chamar create_media."""

    class FakeSPFinalizeOnly:
        def __init__(self):
            self.create_media_calls = 0
            self.poll_calls = 0

        async def create_folder(self, name, parent_code=None):
            return f"sp-{name[:3]}"

        async def create_media(self, *args, **kwargs):
            self.create_media_calls += 1
            return SPMedia(code="x", status="DRAFT", urlsUpload={})

        async def get_upload_urls(self, code):
            return {}

        async def upload_binary(self, url, file_path, content_type):
            pass

        async def poll_status(self, code):
            self.poll_calls += 1
            return "COMPLETED"

        async def move_media(self, folder_code, media_codes):
            pass

    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder("F1", FolderEntry(panda_folder_id="f1", sp_folder_code="sp-F1"))
    m.upsert_video(VideoEntry(
        panda_id="v1", panda_folder="F1", title="Retomada",
        size_bytes=100, state=VideoState.SP_PARTIAL,
        sp_media_code="sp-media-v1",
        sp_embed_url="https://player.scaleup.com.br/embed/sp-media-v1",
    ))
    m.save()

    sp = FakeSPFinalizeOnly()
    await run_pipeline(
        panda=FakePanda(),
        sp=sp,
        manifest=m,
        download_dir=tmp_path / "downloads",
        poll_interval=0,
        finalizer_interval=0,
    )

    assert m.videos["v1"].state == VideoState.DONE
    assert sp.create_media_calls == 0  # upload worker não tocou o vídeo
    assert sp.poll_calls >= 1          # finalizer fez ao menos um poll
