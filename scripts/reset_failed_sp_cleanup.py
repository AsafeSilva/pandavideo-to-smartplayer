"""
Deleta as mídias dos vídeos FAILED no SP e reseta o manifest para PENDING.
Deve ser rodado antes de `python -m src.migrate run` para reprocessar os falhos.

Uso:
    python scripts/reset_failed_sp_cleanup.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json

from src.config import Settings
from src.manifest import Manifest
from src.models import VideoState
from src.smartplayer_client import SmartPlayerClient

MANIFEST_PATH = Path("data/manifest.json")
TOKEN_CACHE   = Path("data/.sp_token_cache.json")


async def main():
    settings = Settings.from_env()
    manifest = Manifest.load(MANIFEST_PATH)

    failed = [v for v in manifest.videos.values() if v.state == VideoState.FAILED]
    if not failed:
        print("Nenhum vídeo em FAILED no manifest.")
        return

    print(f"{len(failed)} vídeo(s) em FAILED encontrado(s):")
    for v in failed:
        print(f"  - {v.title} | sp_media_code={v.sp_media_code or '(vazio)'}")

    async with SmartPlayerClient(
        client_id=settings.sp_client_id,
        client_secret=settings.sp_client_secret,
        user_code=settings.sp_user_code,
        token_cache_path=TOKEN_CACHE,
    ) as sp:
        for v in failed:
            if v.sp_media_code:
                try:
                    await sp.delete_media(v.sp_media_code)
                    print(f"  [SP] deletado: {v.title} ({v.sp_media_code})")
                except Exception as e:
                    print(f"  [SP] erro ao deletar {v.sp_media_code}: {e}")

            # Reseta para PENDING no manifest
            entry = manifest.videos[v.panda_id]
            entry.state = VideoState.PENDING
            entry.sp_media_code = ""
            entry.sp_embed_url = ""
            entry.error = ""
            entry.local_video_path = ""
            entry.local_thumb_path = ""

    manifest.save()
    print(f"\nManifest salvo. {len(failed)} vídeo(s) resetado(s) para PENDING.")
    print("Rode agora: python -m src.migrate run")


if __name__ == "__main__":
    asyncio.run(main())
