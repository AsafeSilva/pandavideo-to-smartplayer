"""
Lista todos os media com status ERROR na pasta Introdução do SP e imprime
o body JSON completo de cada um — para diagnosticar a causa da falha de encoding.

Uso:
    python scripts/probe_sp_error_detail.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import httpx

from src.config import Settings
from src.smartplayer_client import SmartPlayerClient

FOLDER_CODE = "a26b0345f620f971a5ed51d04d7e1e472b434f59"  # Passos Iniciais / Introdução
TOKEN_CACHE = Path("data/.sp_token_cache.json")


async def list_folder_medias(sp: SmartPlayerClient, folder_code: str) -> list:
    headers = await sp._authed_headers()
    medias = []
    page = 0
    while True:
        async with httpx.AsyncClient(timeout=30, http2=True) as c:
            r = await c.get(
                f"{sp._base_url}/medias/names",
                headers=headers,
                params={"folderCode": folder_code, "page": page, "size": 50},
            )
        if r.status_code != 200:
            print(f"Erro ao listar media: {r.status_code} {r.text[:200]}")
            break
        data = r.json()
        items = data.get("content", data) if isinstance(data, dict) else data
        if not items:
            break
        medias.extend(items)
        total_pages = data.get("totalPages", 1) if isinstance(data, dict) else 1
        page += 1
        if page >= total_pages:
            break
    return medias


async def get_media_detail(sp: SmartPlayerClient, media_code: str) -> dict:
    headers = await sp._authed_headers()
    async with httpx.AsyncClient(timeout=30, http2=True) as c:
        r = await c.get(f"{sp._base_url}/medias/{media_code}", headers=headers)
    return r.json()


async def main():
    settings = Settings.from_env()

    async with SmartPlayerClient(
        client_id=settings.sp_client_id,
        client_secret=settings.sp_client_secret,
        user_code=settings.sp_user_code,
        token_cache_path=TOKEN_CACHE,
    ) as sp:
        print(f"Listando media na pasta Introdução (folder={FOLDER_CODE}) ...")
        medias = await list_folder_medias(sp, FOLDER_CODE)
        print(f"Total encontrado: {len(medias)} media(s)\n")

        error_medias = [m for m in medias if m.get("status") == "ERROR"]
        print(f"Media com status ERROR: {len(error_medias)}\n")

        if not error_medias:
            print("Nenhum media em ERROR encontrado nessa pasta.")
            print("Todos os media encontrados:")
            for m in medias:
                print(f"  [{m.get('status')}] {m.get('name')} — code={m.get('code')}")
            return

        for m in error_medias:
            code = m.get("code")
            name = m.get("name")
            print(f"{'='*60}")
            print(f"Nome: {name}")
            print(f"Code: {code}")
            print(f"Status rápido: {m.get('status')}")
            print(f"\nBody completo de GET /medias/{code}:")
            detail = await get_media_detail(sp, code)
            print(json.dumps(detail, indent=2, ensure_ascii=False))
            print()


if __name__ == "__main__":
    asyncio.run(main())
