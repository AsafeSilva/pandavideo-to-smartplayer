"""Cliente assíncrono da API Panda Video."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type,
)


PANDA_BASE_URL = "https://api-v2.pandavideo.com.br"


def _retry_http():
    # Allow fast retries in tests via env var (RETRY_FAST=1)
    fast = os.environ.get("RETRY_FAST") == "1"
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(
            multiplier=0.01 if fast else 1,
            min=0 if fast else 4,
            max=0.1 if fast else 30,
        ),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        reraise=True,
    )


class PandaFolder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    parent_folder_id: Optional[str] = None


class PandaVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    folder_id: Optional[str] = None
    thumbnail: Optional[str] = None
    length: float = 0
    size: float = 0
    storage_size: float = 0
    tags: list[str] = Field(default_factory=list)
    status: str = "unknown"
    created_at: Optional[str] = None
    video_external_id: Optional[str] = None


class PandaClient:
    def __init__(self, api_key: str, base_url: str = PANDA_BASE_URL, timeout: float = 60.0):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            http2=True,
        )

    async def __aenter__(self) -> "PandaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Task 4 — list_folders e list_videos
    # ------------------------------------------------------------------

    @_retry_http()
    async def list_folders(self, parent_folder_id: Optional[str] = None) -> list[PandaFolder]:
        params: dict[str, str] = {}
        if parent_folder_id is not None:
            params["parent_folder_id"] = parent_folder_id
        r = await self._client.get(f"{self._base_url}/folders", params=params)
        r.raise_for_status()
        data = r.json()
        raw = data.get("folders", data) if isinstance(data, dict) else data
        return [PandaFolder.model_validate(item) for item in raw]

    @_retry_http()
    async def _fetch_video_page(self, folder_id: str, page: int, limit: int) -> list[dict]:
        r = await self._client.get(
            f"{self._base_url}/videos",
            params={"folder_id": folder_id, "page": page, "limit": limit},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("videos", data) if isinstance(data, dict) else data

    async def list_videos(self, folder_id: str, limit: int = 100) -> list[PandaVideo]:
        videos: list[PandaVideo] = []
        page = 1
        while True:
            batch = await self._fetch_video_page(folder_id, page, limit)
            if not batch:
                break
            videos.extend(PandaVideo.model_validate(item) for item in batch)
            page += 1
        return videos

    # ------------------------------------------------------------------
    # Task 5 — request_download e poll_download
    # ------------------------------------------------------------------

    @_retry_http()
    async def request_download(
        self,
        video_id: str,
        quality: str = "original",
        title: str = "",
        language: str = "pt-BR",
    ) -> None:
        payload = {
            "quality": quality,
            "format": "video",
            "language": language,
            "video_title": title,
        }
        r = await self._client.post(
            f"{self._base_url}/download-async/{video_id}",
            json=payload,
        )
        if r.status_code not in (200, 201, 202):
            r.raise_for_status()

    @_retry_http()
    async def poll_download(
        self,
        video_id: str,
        quality: str = "original",
        language: str = "pt-BR",
    ) -> Optional[str]:
        """Retorna URL S3 assinada se pronto, None se ainda processando, raise em erro real."""
        r = await self._client.get(
            f"{self._base_url}/download-async/{video_id}/video/{quality}/{language}"
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("url") or data.get("download_url")
        if r.status_code == 400:
            return None
        r.raise_for_status()

    @_retry_http()
    async def get_video(self, video_id: str) -> PandaVideo:
        r = await self._client.get(f"{self._base_url}/videos/{video_id}")
        r.raise_for_status()
        return PandaVideo.model_validate(r.json())

    # ------------------------------------------------------------------
    # Task 6 — download_file em streaming
    # ------------------------------------------------------------------

    async def download_file(self, url: str, dest_path) -> int:
        """Baixa em streaming para dest_path. Retorna bytes escritos."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        # URL S3 assinada — usar client sem header de auth
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30.0)) as raw:
            async with raw.stream("GET", url) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        written += len(chunk)
        return written
