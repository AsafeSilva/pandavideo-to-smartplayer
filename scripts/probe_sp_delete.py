"""Probe para confirmar o formato correto de DELETE /medias/lists no SmartPlayer."""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, ".")
from src.config import Settings
from src.smartplayer_client import SmartPlayerClient


async def main():
    # Pegar o menor vídeo FAILED para testar
    with open("data/manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)

    failed = [
        (v.get("size_bytes", 0), vid, v)
        for vid, v in manifest["videos"].items()
        if v.get("state") == "failed" and v.get("sp_media_code")
    ]
    failed.sort()
    _, test_vid, test_v = failed[0]
    code = test_v["sp_media_code"]
    title = (test_v.get("title") or test_vid)[:60]
    print(f"Testando DELETE para: {title}")
    print(f"sp_media_code: {code}")

    s = Settings.from_env()
    sp = SmartPlayerClient(
        s.sp_client_id, s.sp_client_secret, s.sp_user_code,
        Path("data/.sp_token_cache.json"),
    )
    async with sp:
        h = await sp._authed_headers()
        hj = {**h, "Content-Type": "application/json"}
        base = sp._base_url

        async with httpx.AsyncClient(timeout=30, http2=True) as c:
            # Verificar status atual da media no SP
            r = await c.get(f"{base}/medias/{code}", headers=h)
            print(f"\nStatus atual no SP: GET /medias/{code[:12]}... -> {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"  status={data.get('status')}  size={data.get('size')}  name={str(data.get('name',''))[:50]}")
            elif r.status_code == 404:
                print("  (media nao existe mais no SP)")

            print("\nTestando formatos de body para DELETE /medias/lists:")
            bodies = [
                [{"code": code}],
                [code],
                {"codes": [code]},
                {"mediaCodes": [code]},
                {"mediaCode": code},
            ]
            for body in bodies:
                r = await c.request(
                    "DELETE", f"{base}/medias/lists",
                    headers=hj, content=json.dumps(body).encode(),
                )
                body_str = json.dumps(body)[:60]
                print(f"  body={body_str:<62} -> {r.status_code}  {r.text[:100]}")
                if r.status_code in (200, 204, 202):
                    print("  *** FUNCIONOU ***")
                    break


if __name__ == "__main__":
    asyncio.run(main())
