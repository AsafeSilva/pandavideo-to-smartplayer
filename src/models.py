"""Tipos compartilhados pelo pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class VideoState(str, Enum):
    PENDING = "pending"
    DOWNLOAD_REQUESTED = "download_requested"
    DOWNLOAD_READY = "download_ready"
    DOWNLOADED = "downloaded"
    SP_MEDIA_CREATED = "sp_media_created"
    SP_UPLOAD_URLS_READY = "sp_upload_urls_ready"
    UPLOADING = "uploading"
    SP_PROCESSING = "sp_processing"
    SP_COMPLETED = "sp_completed"
    SP_MOVED = "sp_moved"
    DONE = "done"
    FAILED = "failed"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class FolderEntry:
    panda_folder_id: str
    sp_folder_code: Optional[str] = None


@dataclass
class VideoEntry:
    panda_id: str
    panda_folder: str
    title: str
    description: str = ""
    panda_external_id: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_sec: int = 0
    size_bytes: int = 0
    tags: list[str] = field(default_factory=list)
    state: VideoState = VideoState.PENDING
    local_video_path: Optional[str] = None
    local_thumb_path: Optional[str] = None
    sp_media_code: Optional[str] = None
    sp_embed_url: Optional[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
