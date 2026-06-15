"""
Re-descobre os vídeos de uma pasta específica pelo panda_folder_id e os adiciona
de volta ao manifest como PENDING — sem afetar os outros vídeos já migrados.

Uso:
    python scripts/rediscover_folder.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio

from src.config import Settings
from src.discovery import _collect_videos
from src.manifest import Manifest
from src.panda_client import PandaClient
from src.models import FolderEntry

MANIFEST_PATH  = Path("data/manifest.json")
TOKEN_CACHE    = Path("data/.sp_token_cache.json")

# Pasta alvo — ID e label exatos do manifest
PANDA_FOLDER_ID = "c12c2aa2-158a-4b6c-aab3-025a575aae76"
FOLDER_LABEL    = "EDUCACIONAL | Mentoria Automações Inteligentes / Passos Iniciais / Introdução"
SP_FOLDER_CODE  = "a26b0345f620f971a5ed51d04d7e1e472b434f59"


async def main():
    settings = Settings.from_env()
    manifest = Manifest.load(MANIFEST_PATH)

    # Garante que a pasta está no manifest com o sp_folder_code correto
    manifest.folders[FOLDER_LABEL] = FolderEntry(
        panda_folder_id=PANDA_FOLDER_ID,
        sp_folder_code=SP_FOLDER_CODE,
    )

    async with PandaClient(api_key=settings.panda_api_key) as panda:
        print(f"Descobrindo vídeos em: {FOLDER_LABEL}")
        count = await _collect_videos(panda, manifest, PANDA_FOLDER_ID, FOLDER_LABEL)
        print(f"{count} vídeo(s) novo(s) adicionado(s) ao manifest como PENDING.")

    manifest.save()
    total = len(manifest.videos)
    pending = sum(1 for v in manifest.videos.values() if v.state.value == "pending")
    print(f"Manifest salvo. Total: {total} vídeos | Pending: {pending}")


if __name__ == "__main__":
    asyncio.run(main())
