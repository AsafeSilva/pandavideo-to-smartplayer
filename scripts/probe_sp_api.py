"""Probe para inspecionar o endpoint GET /medias/names da API SmartPlayer."""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, ".")
from src.config import Settings
from src.smartplayer_client import SmartPlayerClient


async def main():
    s = Settings.from_env()
    sp = SmartPlayerClient(
        s.sp_client_id, s.sp_client_secret, s.sp_user_code,
        Path("data/.sp_token_cache.json"),
    )
    async with sp:
        h = await sp._authed_headers()
        base = sp._base_url
        print("Base URL:", base)

        async with httpx.AsyncClient(timeout=30, http2=True) as c:
            # Endpoint correto conforme docs: GET /medias/names
            r = await c.get(
                f"{base}/medias/names",
                headers=h,
                params={"page": 0, "size": 3},
            )
            print(f"\nGET /medias/names  ->  {r.status_code}")
            print(f"Content-Type: {r.headers.get('content-type')}")

            if r.status_code == 200:
                data = r.json()
                print(f"Tipo: {type(data).__name__}")
                if isinstance(data, dict):
                    print(f"Chaves do envelope: {list(data.keys())}")
                    print(f"totalElements: {data.get('totalElements')}")
                    print(f"totalPages:    {data.get('totalPages')}")
                    print(f"size (page):   {data.get('size')}")
                    content = data.get("content", [])
                    print(f"Itens nesta pagina: {len(content)}")
                    if content:
                        print(f"\nChaves de cada item: {list(content[0].keys())}")
                        print("\nPrimeiro item completo:")
                        print(json.dumps(content[0], indent=2, ensure_ascii=False))
                elif isinstance(data, list):
                    print(f"Lista com {len(data)} itens")
                    if data:
                        print(json.dumps(data[0], indent=2, ensure_ascii=False))
            else:
                print(f"Corpo: {r.text[:500]}")


if __name__ == "__main__":
    asyncio.run(main())
