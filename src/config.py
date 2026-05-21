"""Carregamento e validação de configuração via .env."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} ausente em .env — preencha e tente novamente")
    return v


@dataclass
class Settings:
    panda_api_key: str
    sp_client_id: str
    sp_client_secret: str
    sp_user_code: str
    max_download_concurrency: int = 3
    max_upload_concurrency: int = 3
    max_disk_usage_gb: int = 10
    panda_quality: str = "original"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None) -> "Settings":
        load_dotenv(dotenv_path)
        return cls(
            panda_api_key=_require("PANDA_API_KEY"),
            sp_client_id=_require("SMARTPLAYER_CLIENT_ID"),
            sp_client_secret=_require("SMARTPLAYER_CLIENT_SECRET"),
            sp_user_code=_require("SMARTPLAYER_USER_CODE"),
            max_download_concurrency=int(os.environ.get("MAX_DOWNLOAD_CONCURRENCY", "3")),
            max_upload_concurrency=int(os.environ.get("MAX_UPLOAD_CONCURRENCY", "3")),
            max_disk_usage_gb=int(os.environ.get("MAX_DISK_USAGE_GB", "10")),
            panda_quality=os.environ.get("PANDA_QUALITY", "original"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
