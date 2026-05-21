"""Discovery: lista pastas/vídeos do Panda e popula o manifest."""
from __future__ import annotations

from datetime import datetime, timezone

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState


async def discover(panda, manifest: Manifest, prefix: str) -> None:
    """Popula `manifest` com pastas que comecem com `prefix` e seus vídeos `converted`."""
    folders = await panda.list_folders()
    selected = [f for f in folders if f.name.startswith(prefix)]
    manifest.discovered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for folder in selected:
        manifest.upsert_folder(
            folder.name,
            FolderEntry(panda_folder_id=folder.id, sp_folder_code=None),
        )
        videos = await panda.list_videos(folder.id)
        for v in videos:
            if v.status != "converted":
                continue
            if v.id in manifest.videos:
                continue
            manifest.upsert_video(VideoEntry(
                panda_id=v.id,
                panda_folder=folder.name,
                title=v.title,
                description=v.description,
                thumbnail_url=v.thumbnail,
                duration_sec=v.length,
                size_bytes=v.size,
                tags=list(v.tags),
                state=VideoState.PENDING,
            ))

    manifest.save()
