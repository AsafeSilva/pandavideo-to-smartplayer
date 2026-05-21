"""Cliente assíncrono da API SmartPlayer (Scaleup)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type,
)


SP_AUTH_URL = "https://services.scaleup.com.br/authentication/v1/oauth/token"


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
SP_BASE_URL = "https://services.scaleup.com.br/backoffice/v1"
SP_EMBED_URL_TEMPLATE = "https://player.scaleup.com.br/embed/{code}"
REFRESH_WINDOW_MIN = 5


class SPMedia(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str
    status: str
    urlsUpload: Optional[dict] = None


class SmartPlayerClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_code: str,
        token_cache_path: Path,
        base_url: str = SP_BASE_URL,
        timeout: float = 60.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_code = user_code
        self._token_cache_path = Path(token_cache_path)
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            http2=True,
        )

    async def __aenter__(self) -> "SmartPlayerClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    @_retry_http()
    async def get_token(self) -> str:
        cached = self._read_token_cache()
        if cached and not self._near_expiry(cached["expires_at"]):
            return cached["access_token"]

        r = await self._client.post(
            SP_AUTH_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        r.raise_for_status()
        data = r.json()
        access = data["access_token"]
        ttl = int(data.get("expires_in", 604800))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        self._write_token_cache(access, expires_at)
        return access

    def _read_token_cache(self) -> Optional[dict]:
        if not self._token_cache_path.exists():
            return None
        try:
            return json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None

    def _write_token_cache(self, access_token: str, expires_at: str) -> None:
        self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_cache_path.write_text(
            json.dumps({"access_token": access_token, "expires_at": expires_at}),
            encoding="utf-8",
        )

    @staticmethod
    def _near_expiry(expires_at_iso: str) -> bool:
        expires = datetime.fromisoformat(expires_at_iso)
        return expires - datetime.now(timezone.utc) < timedelta(minutes=REFRESH_WINDOW_MIN)

    async def _authed_headers(self) -> dict[str, str]:
        tok = await self.get_token()
        return {
            "Authorization": f"Bearer {tok}",
            "X-User-Code": self._user_code,
        }

    # -----------------------------------------------------------------------
    # Task 8 — folders
    # -----------------------------------------------------------------------

    @_retry_http()
    async def create_folder(self, name: str, parent_code: Optional[str] = None) -> str:
        headers = await self._authed_headers()
        headers["Content-Type"] = "application/json"
        params: dict[str, str] = {}
        if parent_code is not None:
            params["root-folder-code"] = parent_code
        r = await self._client.post(
            f"{self._base_url}/folders",
            headers=headers,
            params=params,
            json={"name": name},
        )
        r.raise_for_status()
        data = r.json()
        return data["code"]

    # -----------------------------------------------------------------------
    # Task 9 — media lifecycle
    # -----------------------------------------------------------------------

    @_retry_http()
    async def create_media(
        self,
        name: str,
        description: str,
        external_id: str,
        total_size: int,
        public_media: bool = True,
    ) -> str:
        headers = await self._authed_headers()
        headers["Content-Type"] = "application/json"
        payload = [{
            "name": name,
            "description": description,
            "externalId": external_id,
            "totalSize": total_size,
            "publicMedia": public_media,
        }]
        r = await self._client.post(
            f"{self._base_url}/medias",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data[0]["code"]
        return data["code"]

    @_retry_http()
    async def get_upload_urls(self, media_code: str) -> dict[str, str]:
        headers = await self._authed_headers()
        r = await self._client.get(
            f"{self._base_url}/medias/{media_code}",
            headers=headers,
        )
        r.raise_for_status()
        urls = r.json().get("urlsUpload") or {}
        return urls

    async def upload_binary(self, url: str, file_path: "Path | str", content_type: str) -> None:
        path = Path(file_path)
        size = path.stat().st_size

        async def _iter_file(f, chunk_size: int = 1024 * 1024):
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30.0)) as raw:
            with path.open("rb") as f:
                r = await raw.put(
                    url,
                    content=_iter_file(f),
                    headers={
                        "Content-Type": content_type,
                        "Content-Length": str(size),
                    },
                )
        r.raise_for_status()

    @_retry_http()
    async def poll_status(self, media_code: str) -> str:
        headers = await self._authed_headers()
        r = await self._client.get(
            f"{self._base_url}/medias/{media_code}",
            headers=headers,
        )
        r.raise_for_status()
        return r.json().get("status", "UNKNOWN")


def build_embed_url(media_code: str) -> str:
    return SP_EMBED_URL_TEMPLATE.format(code=media_code)
