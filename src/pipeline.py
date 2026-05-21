"""Pipeline assíncrono: workers de download e upload."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.manifest import Manifest
from src.models import FolderEntry, VideoState
from src.smartplayer_client import build_embed_url

logger = logging.getLogger(__name__)


async def download_one(
    panda,
    manifest: Manifest,
    video_id: str,
    download_dir: Path,
    quality: str = "original",
    poll_interval: float = 30.0,
    poll_timeout: float = 60 * 60,
) -> None:
    """Executa o sub-pipeline de download para um único vídeo, atualizando o manifest."""
    v = manifest.videos[video_id]

    if v.state == VideoState.PENDING:
        await panda.request_download(video_id, quality, v.title)
        manifest.transition(video_id, VideoState.DOWNLOAD_REQUESTED)

    if v.state in (VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY):
        elapsed = 0.0
        url: str | None = None
        while elapsed < poll_timeout:
            url = await panda.poll_download(video_id, quality)
            if url:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        if not url:
            raise TimeoutError(f"poll_download timeout para {video_id}")
        manifest.transition(video_id, VideoState.DOWNLOAD_READY)
        download_dir.mkdir(parents=True, exist_ok=True)
        dest = download_dir / f"{video_id}.mp4"
        await panda.download_file(url, dest)
        manifest.transition(
            video_id, VideoState.DOWNLOADED,
            local_video_path=str(dest),
        )


SP_TERMINAL_STATUSES = {"COMPLETED"}
SP_ERROR_STATUSES = {"ERROR"}


async def upload_one(
    sp,
    manifest: Manifest,
    video_id: str,
    poll_interval: float = 60.0,
    poll_timeout: float = 2 * 60 * 60,
    cleanup: bool = True,
) -> None:
    """Sub-pipeline: vídeo no disco -> media SP -> upload -> encoding -> DONE."""
    v = manifest.videos[video_id]

    if v.state == VideoState.DOWNLOADED:
        code = await sp.create_media(
            name=v.title,
            description=v.description,
            external_id=v.panda_id,
            total_size=v.size_bytes,
        )
        manifest.transition(video_id, VideoState.SP_MEDIA_CREATED, sp_media_code=code)

    if v.state == VideoState.SP_MEDIA_CREATED:
        manifest.transition(video_id, VideoState.SP_UPLOAD_URLS_READY)

    if v.state == VideoState.SP_UPLOAD_URLS_READY:
        urls = await sp.get_upload_urls(v.sp_media_code)
        await sp.upload_binary(urls["urlUploadVideo"], v.local_video_path, "video/mp4")
        if v.local_thumb_path:
            await sp.upload_binary(urls["urlUploadPoster"], v.local_thumb_path, "image/jpeg")
        manifest.transition(video_id, VideoState.UPLOADING)
        manifest.transition(video_id, VideoState.SP_PROCESSING)

    if v.state == VideoState.SP_PROCESSING:
        elapsed = 0.0
        status = None
        while elapsed < poll_timeout:
            status = await sp.poll_status(v.sp_media_code)
            if status in SP_TERMINAL_STATUSES:
                break
            if status in SP_ERROR_STATUSES:
                raise RuntimeError(f"SP retornou ERROR para {video_id}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        if status not in SP_TERMINAL_STATUSES:
            raise TimeoutError(f"poll_status timeout para {video_id}")
        manifest.transition(
            video_id, VideoState.SP_COMPLETED,
            sp_embed_url=build_embed_url(v.sp_media_code),
        )

    if v.state == VideoState.SP_COMPLETED:
        if cleanup and v.local_video_path:
            try:
                Path(v.local_video_path).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("cleanup falhou para %s: %s", video_id, e)
            if v.local_thumb_path:
                Path(v.local_thumb_path).unlink(missing_ok=True)
        manifest.transition(video_id, VideoState.DONE)


async def _ensure_sp_folder(sp, manifest: Manifest, folder_name: str) -> str:
    f = manifest.folders.get(folder_name)
    if f and f.sp_folder_code:
        return f.sp_folder_code
    code = await sp.create_folder(folder_name)
    new_entry = FolderEntry(
        panda_folder_id=f.panda_folder_id if f else "",
        sp_folder_code=code,
    )
    manifest.upsert_folder(folder_name, new_entry)
    manifest.save()
    return code


async def run_pipeline(
    panda,
    sp,
    manifest: Manifest,
    download_dir: Path,
    max_download_concurrency: int = 3,
    max_upload_concurrency: int = 3,
    poll_interval: float = 30.0,
    quality: str = "original",
) -> None:
    """Orquestra workers de download e upload via filas asyncio."""
    # Garante pasta no SP para cada folder do manifest
    for folder_name in list(manifest.folders.keys()):
        await _ensure_sp_folder(sp, manifest, folder_name)

    # Filas
    to_download: asyncio.Queue[str] = asyncio.Queue()
    to_upload: asyncio.Queue[str] = asyncio.Queue()

    # Popula com vídeos pendentes
    pre_download = (VideoState.PENDING, VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY)
    pre_upload = (VideoState.DOWNLOADED, VideoState.SP_MEDIA_CREATED,
                  VideoState.SP_UPLOAD_URLS_READY, VideoState.UPLOADING, VideoState.SP_PROCESSING,
                  VideoState.SP_COMPLETED)

    for v in manifest.videos_in_state(*pre_download):
        await to_download.put(v.panda_id)
    for v in manifest.videos_in_state(*pre_upload):
        await to_upload.put(v.panda_id)

    sentinel = "__STOP__"

    async def download_worker():
        while True:
            vid = await to_download.get()
            if vid == sentinel:
                to_download.task_done()
                return
            try:
                await download_one(panda, manifest, vid, download_dir, quality, poll_interval)
                await to_upload.put(vid)
            except Exception as e:
                manifest.mark_failed(vid, f"download: {e!r}")
                logger.exception("download falhou para %s", vid)
            finally:
                to_download.task_done()

    async def upload_worker():
        while True:
            vid = await to_upload.get()
            if vid == sentinel:
                to_upload.task_done()
                return
            try:
                await upload_one(sp, manifest, vid, poll_interval)
            except Exception as e:
                manifest.mark_failed(vid, f"upload: {e!r}")
                logger.exception("upload falhou para %s", vid)
            finally:
                to_upload.task_done()

    downloaders = [asyncio.create_task(download_worker()) for _ in range(max_download_concurrency)]
    uploaders = [asyncio.create_task(upload_worker()) for _ in range(max_upload_concurrency)]

    # Aguarda fila de download esvaziar e sinaliza downloaders
    await to_download.join()
    for _ in downloaders:
        await to_download.put(sentinel)
    await asyncio.gather(*downloaders)

    # Aguarda fila de upload esvaziar e sinaliza uploaders
    await to_upload.join()
    for _ in uploaders:
        await to_upload.put(sentinel)
    await asyncio.gather(*uploaders)
