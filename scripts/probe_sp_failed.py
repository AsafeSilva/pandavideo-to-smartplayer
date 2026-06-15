"""
Busca o status atual de cada vídeo failed no manifest diretamente na API do SP
e imprime o body JSON completo — para diagnosticar a causa do encoding ERROR.

Uso:
    python scripts/probe_sp_failed.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json

from src.config import Settings
from src.smartplayer_client import SmartPlayerClient

MANIFEST_PATH = Path("data/manifest.json")
TOKEN_CACHE   = Path("data/.sp_token_cache.json")


async def main():
    settings = Settings.from_env()
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    failed = [
        (v.get("title", k), v["sp_media_code"])
        for k, v in data["videos"].items()
        if v["state"] == "failed" and v.get("sp_media_code")
    ]

    if not failed:
        print("Nenhum vídeo failed com sp_media_code encontrado.")
        return

    print(f"Consultando {len(failed)} vídeo(s) failed no SP...\n")

    async with SmartPlayerClient(
        client_id=settings.sp_client_id,
        client_secret=settings.sp_client_secret,
        user_code=settings.sp_user_code,
        token_cache_path=TOKEN_CACHE,
    ) as sp:
        headers = await sp._authed_headers()

        for title, code in failed:
            print("=" * 60)
            print(f"Título : {title}")
            print(f"SP code: {code}")

            import httpx
            async with httpx.AsyncClient(timeout=30, http2=True) as c:
                r = await c.get(
                    f"{sp._base_url}/medias/{code}",
                    headers=headers,
                )

            if r.status_code == 404:
                print("Resultado: 404 — mídia não existe no SP")
            else:
                body = r.json()
                status = body.get("status", "UNKNOWN")
                print(f"Status  : {status}")
                print(f"Body    : {json.dumps(body, indent=2, ensure_ascii=False)}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
