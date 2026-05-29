"""Pipeline assíncrono: workers de download e upload."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from src.manifest import Manifest
from src.models import FolderEntry, VideoState
from src.smartplayer_client import build_embed_url

if TYPE_CHECKING:
    from src.dashboard import LiveDashboard

logger = logging.getLogger(__name__)


def _disk_used_gb(download_dir: Path) -> float:
    if not download_dir.exists():
        return 0.0
    return sum(f.stat().st_size for f in download_dir.glob("*.mp4") if f.exists()) / (1024 ** 3)


async def download_one(
    panda,
    manifest: Manifest,
    video_id: str,
    download_dir: Path,
    quality: str = "original",
    poll_interval: float = 30.0,
    poll_timeout: float = 60 * 60,
    dashboard: "LiveDashboard | None" = None,
) -> None:
    """Executa o sub-pipeline de download para um único vídeo, atualizando o manifest."""
    v = manifest.videos[video_id]
    title = v.title or video_id

    # O endpoint download-async usa o video_external_id (Bunny CDN), não o panda_id
    if not v.panda_external_id:
        pv = await panda.get_video(video_id)
        manifest.transition(video_id, v.state, panda_external_id=pv.video_external_id)
        v = manifest.videos[video_id]
    dl_id = v.panda_external_id or video_id

    if v.state == VideoState.PENDING:
        size_mb = v.size_bytes / (1024 ** 2) if v.size_bytes else 0
        logger.info("[download] requisitando: %s (%.0f MB)", title, size_mb)
        if dashboard:
            dashboard.on_download_phase(video_id, "requisitando")
        await panda.request_download(dl_id, quality, v.title)
        manifest.transition(video_id, VideoState.DOWNLOAD_REQUESTED)

    if v.state in (VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY):
        logger.info("[download] aguardando Panda processar: %s", title)
        if dashboard:
            dashboard.on_download_phase(video_id, "ag. Panda...")
        elapsed = 0.0
        url: str | None = None
        while elapsed < poll_timeout:
            url = await panda.poll_download(dl_id, quality)
            if url:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed % 300 < poll_interval:
                logger.info("[download] ainda aguardando Panda: %s (%.0fs)", title, elapsed)
        if not url:
            raise TimeoutError(f"poll_download timeout para {video_id}")
        manifest.transition(video_id, VideoState.DOWNLOAD_READY)
        download_dir.mkdir(parents=True, exist_ok=True)
        dest = download_dir / f"{video_id}.mp4"
        logger.info("[download] baixando arquivo: %s → %s", title, dest.name)
        if dashboard:
            dashboard.on_download_phase(video_id, "baixando...")
        await panda.download_file(url, dest)
        manifest.transition(
            video_id, VideoState.DOWNLOADED,
            local_video_path=str(dest),
        )
        logger.info("[download] concluído: %s", title)
        if dashboard:
            dashboard.on_download_phase(video_id, "concluído")


SP_TERMINAL_STATUSES = {"COMPLETED"}
SP_ERROR_STATUSES = {"ERROR"}


async def upload_one(
    sp,
    manifest: Manifest,
    video_id: str,
    poll_interval: float = 60.0,
    poll_timeout: float = 2 * 60 * 60,
    cleanup: bool = True,
    dashboard: "LiveDashboard | None" = None,
) -> None:
    """Sub-pipeline: vídeo no disco -> media SP -> upload -> encoding -> DONE."""
    v = manifest.videos[video_id]
    title = v.title or video_id

    if v.state == VideoState.DOWNLOADED:
        display_title = v.title
        size_mb = v.size_bytes / (1024 ** 2) if v.size_bytes else 0
        logger.info("[upload] criando media SP: %s (%.0f MB)", display_title, size_mb)
        if dashboard:
            dashboard.on_upload_phase(video_id, "criando media")
        media = await sp.create_media(
            name=display_title,
            description=v.description,
            external_id=v.panda_id,
            total_size=v.size_bytes,
        )
        manifest.transition(video_id, VideoState.SP_MEDIA_CREATED, sp_media_code=media.code)
        # urlsUpload só vem na criação — faz upload imediatamente
        urls = media.urlsUpload or {}
        if urls.get("urlUploadVideo"):
            logger.info("[upload] enviando para SP: %s", title)
            if dashboard:
                dashboard.on_upload_phase(video_id, "enviando")
            await sp.upload_binary(urls["urlUploadVideo"], v.local_video_path, "video/mp4")
            if v.local_thumb_path and urls.get("urlUploadPoster"):
                await sp.upload_binary(urls["urlUploadPoster"], v.local_thumb_path, "image/jpeg")
            manifest.transition(video_id, VideoState.UPLOADING)
            manifest.transition(video_id, VideoState.SP_PROCESSING)

    # Fallback: se retomar de SP_MEDIA_CREATED ou SP_UPLOAD_URLS_READY sem URL salva
    if v.state in (VideoState.SP_MEDIA_CREATED, VideoState.SP_UPLOAD_URLS_READY):
        urls = await sp.get_upload_urls(v.sp_media_code)
        if urls.get("urlUploadVideo"):
            logger.info("[upload] enviando para SP (retomada): %s", title)
            if dashboard:
                dashboard.on_upload_phase(video_id, "enviando")
            await sp.upload_binary(urls["urlUploadVideo"], v.local_video_path, "video/mp4")
            if v.local_thumb_path and urls.get("urlUploadPoster"):
                await sp.upload_binary(urls["urlUploadPoster"], v.local_thumb_path, "image/jpeg")
            manifest.transition(video_id, VideoState.UPLOADING)
            manifest.transition(video_id, VideoState.SP_PROCESSING)

    if v.state == VideoState.SP_PROCESSING:
        logger.info("[upload] aguardando encoding SP: %s", title)
        if dashboard:
            dashboard.on_upload_phase(video_id, "encoding...")
        # Pre-cria pasta antes do loop para poder tentar move a cada ciclo
        folder_code = await _ensure_sp_folder(sp, manifest, v.panda_folder)
        elapsed = 0.0
        status = None
        while elapsed < poll_timeout:
            status = await sp.poll_status(v.sp_media_code)
            if status in SP_TERMINAL_STATUSES:
                break
            if status in SP_ERROR_STATUSES:
                raise RuntimeError(f"SP retornou ERROR para {video_id}")
            # Tentativa otimista de move: se funcionar, libera o worker agora
            try:
                await sp.move_media(folder_code, [v.sp_media_code])
                logger.info("[upload] move antecipado OK — worker liberado: %s", title)
                if cleanup and v.local_video_path:
                    try:
                        Path(v.local_video_path).unlink(missing_ok=True)
                    except OSError as e:
                        logger.warning("cleanup antecipado falhou para %s: %s", video_id, e)
                    if v.local_thumb_path:
                        Path(v.local_thumb_path).unlink(missing_ok=True)
                manifest.transition(
                    video_id, VideoState.SP_PARTIAL,
                    sp_embed_url=build_embed_url(v.sp_media_code),
                )
                if dashboard:
                    dashboard.on_upload_phase(video_id, "sp_partial")
                return  # worker liberado; finalizer cuida do resto
            except (httpx.HTTPStatusError, httpx.TransportError):
                pass  # ainda não pode mover, continua polling
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed % 300 < poll_interval:
                logger.info("[upload] encoding em andamento: %s status=%s (%.0fs)", title, status, elapsed)
        if status not in SP_TERMINAL_STATUSES:
            raise TimeoutError(f"poll_status timeout para {video_id}")
        manifest.transition(
            video_id, VideoState.SP_COMPLETED,
            sp_embed_url=build_embed_url(v.sp_media_code),
        )

    if v.state == VideoState.SP_COMPLETED:
        folder_code = await _ensure_sp_folder(sp, manifest, v.panda_folder)
        await sp.move_media(folder_code, [v.sp_media_code])
        manifest.transition(video_id, VideoState.SP_MOVED)

    if v.state == VideoState.SP_MOVED:
        if cleanup and v.local_video_path:
            try:
                Path(v.local_video_path).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("cleanup falhou para %s: %s", video_id, e)
            if v.local_thumb_path:
                Path(v.local_thumb_path).unlink(missing_ok=True)
        manifest.transition(video_id, VideoState.DONE)
        logger.info("[DONE] %s", title)
        if dashboard:
            dashboard.on_upload_phase(video_id, "DONE")


async def _ensure_sp_folder(sp, manifest: Manifest, folder_name: str) -> str:
    f = manifest.folders.get(folder_name)
    if f and f.sp_folder_code:
        return f.sp_folder_code

    if " / " in folder_name:
        # "EDUCACIONAL | X / Subpasta" → cria pai primeiro, depois filho
        parent_name, child_name = folder_name.rsplit(" / ", 1)
        parent_code = await _ensure_sp_folder(sp, manifest, parent_name)
        code = await sp.create_folder(child_name, parent_code=parent_code)
    else:
        code = await sp.create_folder(folder_name)

    new_entry = FolderEntry(
        panda_folder_id=f.panda_folder_id if f else "",
        sp_folder_code=code,
    )
    manifest.upsert_folder(folder_name, new_entry)
    manifest.save()
    return code


async def background_finalizer(
    sp,
    manifest: Manifest,
    finalizer_interval: float = 300.0,
) -> None:
    """Monitora vídeos SP_PARTIAL até COMPLETED → DONE ou ERROR → FAILED.

    Deve ser chamado após os upload workers encerrarem, garantindo que o conjunto
    de SP_PARTIAL está completo e sem race condition de escrita.
    """
    while True:
        partial = manifest.videos_in_state(VideoState.SP_PARTIAL)
        if not partial:
            return
        for v in partial:
            try:
                status = await sp.poll_status(v.sp_media_code)
                if status in SP_TERMINAL_STATUSES:
                    manifest.transition(v.panda_id, VideoState.DONE)
                    logger.info("[finalizer] DONE: %s", v.title)
                elif status in SP_ERROR_STATUSES:
                    manifest.mark_failed(v.panda_id, "SP encoding ERROR após move antecipado")
                    logger.error("[finalizer] FAILED: %s status=%s", v.title, status)
                else:
                    logger.debug("[finalizer] ainda em encoding: %s status=%s", v.title, status)
            except Exception as e:
                logger.warning("[finalizer] erro transitório para %s: %s", v.panda_id, e)
        if not manifest.videos_in_state(VideoState.SP_PARTIAL):
            return
        await asyncio.sleep(finalizer_interval)


async def run_pipeline(
    panda,
    sp,
    manifest: Manifest,
    download_dir: Path,
    max_download_concurrency: int = 3,
    max_upload_concurrency: int = 3,
    poll_interval: float = 30.0,
    quality: str = "original",
    limit: int | None = None,
    max_disk_gb: float | None = None,
    dashboard: "LiveDashboard | None" = None,
    finalizer_interval: float = 300.0,
) -> None:
    """Orquestra workers de download e upload via filas asyncio."""
    # Filas
    to_download: asyncio.Queue[str] = asyncio.Queue()
    to_upload: asyncio.Queue[str] = asyncio.Queue()

    # Popula com vídeos pendentes
    pre_download = (VideoState.PENDING, VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY)
    # SP_PARTIAL excluído: gerenciado pelo background_finalizer, não pelos upload workers
    pre_upload = (VideoState.DOWNLOADED, VideoState.SP_MEDIA_CREATED,
                  VideoState.SP_UPLOAD_URLS_READY, VideoState.UPLOADING, VideoState.SP_PROCESSING,
                  VideoState.SP_COMPLETED, VideoState.SP_MOVED)

    pending = manifest.videos_in_state(*pre_download)
    if limit is not None:
        pending = pending[:limit]
    for v in pending:
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
            v = manifest.videos[vid]
            size_mb = v.size_bytes / (1024 ** 2) if v.size_bytes else 0
            if dashboard:
                dashboard.on_download_start(vid, v.title or vid, size_mb)
            if max_disk_gb is not None:
                _poll = 0.01 if os.environ.get("RETRY_FAST") == "1" else 30.0
                _used = _disk_used_gb(download_dir)
                while _used >= max_disk_gb:
                    if dashboard:
                        dashboard.on_download_phase(vid, "ag. disco...")
                    logger.info(
                        "[disk] aguardando espaço — uso atual %.1f GB / %.0f GB",
                        _used,
                        max_disk_gb,
                    )
                    await asyncio.sleep(_poll)
                    _used = _disk_used_gb(download_dir)
            try:
                await download_one(panda, manifest, vid, download_dir, quality, poll_interval, dashboard=dashboard)
                await to_upload.put(vid)
            except Exception as e:
                manifest.mark_failed(vid, f"download: {e!r}")
                logger.exception("download falhou para %s", vid)
            finally:
                if dashboard:
                    dashboard.on_download_done(vid)
                to_download.task_done()

    async def upload_worker():
        while True:
            vid = await to_upload.get()
            if vid == sentinel:
                to_upload.task_done()
                return
            v = manifest.videos[vid]
            if dashboard:
                dashboard.on_upload_start(vid, v.title or vid)
            try:
                await upload_one(sp, manifest, vid, poll_interval, dashboard=dashboard)
            except Exception as e:
                manifest.mark_failed(vid, f"upload: {e!r}")
                logger.exception("upload falhou para %s", vid)
            finally:
                if dashboard:
                    dashboard.on_upload_done(vid)
                to_upload.task_done()

    total = len(manifest.videos)
    logger.info(
        "[pipeline] iniciando — %d vídeos na fila de download, %d retomando upload",
        to_download.qsize(), to_upload.qsize(),
    )

    def _log_progress() -> None:
        counts: dict[str, int] = {}
        for v in manifest.videos.values():
            counts[v.state.value] = counts.get(v.state.value, 0) + 1
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)
        in_progress = {k: n for k, n in counts.items() if k not in ("done", "failed")}
        parts = ", ".join(f"{k}:{n}" for k, n in sorted(in_progress.items()))
        logger.info("[progresso] %d/%d concluídos, %d falhas — %s", done, total, failed, parts or "nenhum em andamento")

    async def progress_reporter() -> None:
        while True:
            await asyncio.sleep(60)
            _log_progress()

    reporter = asyncio.create_task(progress_reporter())

    try:
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

        # Após workers finalizarem, monitora SP_PARTIAL até todos chegarem a DONE/FAILED
        await background_finalizer(sp, manifest, finalizer_interval)
    finally:
        reporter.cancel()
        await asyncio.gather(reporter, return_exceptions=True)

    _log_progress()
    logger.info("[pipeline] finalizado")
