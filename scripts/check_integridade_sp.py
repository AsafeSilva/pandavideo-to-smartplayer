"""
Verifica se cada vídeo migrado chegou íntegro ao SmartPlayer.

Rodar ANTES de cancelar o Panda: `done` no manifest só prova que o pipeline
terminou, não que a mídia está inteira. Um download truncado, um encoding em
PARTIAL_COMPLETED ou um move antecipado produzem mídia aceita pelo SP e curta.

A duração é o indicador confiável: o tamanho em bytes muda com o transcode, a
duração não. Se o arquivo subiu cortado, a duração no SP fica menor que a do Panda.

Uso:
    python scripts/check_integridade_sp.py
    python scripts/check_integridade_sp.py --tolerancia 5
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from src.config import Settings
from src.manifest import Manifest
from src.models import VideoState
from src.smartplayer_client import SmartPlayerClient

MANIFEST_PATH = Path("data/manifest.json")
TOKEN_CACHE = Path("data/.sp_token_cache.json")
OK_STATUSES = {"COMPLETED", "PARTIAL_COMPLETED"}


async def fetch_sp_medias(settings: Settings) -> dict[str, dict]:
    """Todas as mídias do SP indexadas por code."""
    out: dict[str, dict] = {}
    async with SmartPlayerClient(
        client_id=settings.sp_client_id,
        client_secret=settings.sp_client_secret,
        user_code=settings.sp_user_code,
        token_cache_path=TOKEN_CACHE,
    ) as sp:
        headers = await sp._authed_headers()
        async with httpx.AsyncClient(timeout=60) as c:
            page = 0
            while page <= 50:
                r = await c.get(f"{sp._base_url}/medias/names", headers=headers,
                                params={"page": page, "size": 200})
                r.raise_for_status()
                batch = r.json().get("content", [])
                for m in batch:
                    out[m["code"]] = m
                if len(batch) < 200:
                    break
                page += 1
    return out


async def fetch_one(settings: Settings, code: str) -> dict | None:
    """Consulta uma mídia pelo code.

    Necessário porque /medias/names OMITE mídias em PARTIAL_COMPLETED — ausência
    na listagem não prova que a mídia sumiu, só que ela não está COMPLETED.
    """
    async with SmartPlayerClient(
        client_id=settings.sp_client_id,
        client_secret=settings.sp_client_secret,
        user_code=settings.sp_user_code,
        token_cache_path=TOKEN_CACHE,
    ) as sp:
        headers = await sp._authed_headers()
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{sp._base_url}/medias/{code}", headers=headers)
            return r.json() if r.status_code == 200 else None


async def main() -> None:
    tol = 2.0
    if "--tolerancia" in sys.argv:
        tol = float(sys.argv[sys.argv.index("--tolerancia") + 1])

    settings = Settings.from_env()
    manifest = Manifest.load(MANIFEST_PATH)
    print("Consultando SmartPlayer...")
    sp_medias = await fetch_sp_medias(settings)

    done = [v for v in manifest.videos.values() if v.state == VideoState.DONE]
    sem_code, ausentes, curtos, status_ruim, vazios, ok = [], [], [], [], [], 0

    for v in done:
        if not v.sp_media_code:
            sem_code.append((v, None))
            continue
        m = sp_medias.get(v.sp_media_code)
        if m is None:
            # não está na listagem: confirma pelo endpoint individual antes de acusar
            m = await fetch_one(settings, v.sp_media_code)
        if m is None:
            ausentes.append((v, None))
            continue
        if (m.get("status") or "") not in OK_STATUSES:
            status_ruim.append((v, m))
            continue
        if not (m.get("size") or 0):
            vazios.append((v, m))
            continue
        sp_dur = m.get("duration") or 0
        if v.duration_sec and sp_dur < v.duration_sec - tol:
            curtos.append((v, m))
            continue
        ok += 1

    print(f"\nVídeos DONE no manifest:      {len(done)}")
    print(f"Mídias existentes no SP:      {len(sp_medias)}")
    print(f"\n  íntegros:                   {ok}")
    print(f"  sem sp_media_code:          {len(sem_code)}")
    print(f"  code não existe mais no SP: {len(ausentes)}")
    print(f"  status inesperado:          {len(status_ruim)}")
    print(f"  tamanho zero:               {len(vazios)}")
    print(f"  duração menor que a origem: {len(curtos)}  (tolerância {tol:.0f}s)")

    problemas = [("SEM CODE", sem_code), ("AUSENTE NO SP", ausentes),
                 ("STATUS", status_ruim), ("VAZIO", vazios), ("CURTO", curtos)]
    achou = False
    for rot, lista in problemas:
        for v, m in lista:
            achou = True
            det = ""
            if m:
                det = (f" | SP: {m.get('durationValue')} ({m.get('duration')}s), "
                       f"{m.get('sizeValue')}, {m.get('status')}")
            print(f"\n[{rot}] {v.panda_folder} | {v.title}")
            print(f"   Panda: {v.duration_sec}s{det}")
            print(f"   code: {v.sp_media_code}")

    if not achou:
        print("\nTodos os vídeos migrados estão íntegros no SmartPlayer.")
        print("Seguro cancelar o Panda.")


if __name__ == "__main__":
    asyncio.run(main())
