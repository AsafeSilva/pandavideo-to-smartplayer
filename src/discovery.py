"""Discovery: lista pastas/vídeos do Panda e popula o manifest."""
from __future__ import annotations

from datetime import datetime, timezone

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState


async def _collect_videos(panda, manifest: Manifest, folder_id: str, folder_label: str) -> int:
    """Lista vídeos de uma pasta (e subpastas) e insere no manifest. Retorna count inseridos."""
    count = 0
    videos = await panda.list_videos(folder_id)
    for v in videos:
        if v.status.upper() != "CONVERTED":
            continue
        if v.id in manifest.videos:
            continue
        manifest.upsert_video(VideoEntry(
            panda_id=v.id,
            panda_folder=folder_label,
            title=v.title,
            description=v.description,
            thumbnail_url=v.thumbnail,
            duration_sec=int(v.length),
            size_bytes=int(v.storage_size or v.size or 0),
            tags=list(v.tags),
            state=VideoState.PENDING,
        ))
        count += 1

    # Se a pasta não tem vídeos diretos, vasculha subpastas
    if count == 0:
        subfolders = await panda.list_folders(parent_folder_id=folder_id)
        for sub in subfolders:
            count += await _collect_videos(panda, manifest, sub.id, folder_label)

    return count


async def discover(panda, manifest: Manifest, prefix: str) -> None:
    """Popula `manifest` com pastas que comecem com `prefix` e seus vídeos CONVERTED."""
    folders = await panda.list_folders()
    selected = [f for f in folders if f.name.startswith(prefix)]
    manifest.discovered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for folder in selected:
        existing = manifest.folders.get(folder.name)
        manifest.upsert_folder(
            folder.name,
            FolderEntry(
                panda_folder_id=folder.id,
                sp_folder_code=existing.sp_folder_code if existing else None,
            ),
        )
        await _collect_videos(panda, manifest, folder.id, folder.name)

    manifest.save()
