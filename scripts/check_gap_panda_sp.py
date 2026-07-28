"""
Audita a cobertura da migração: o que existe no Panda e ainda não está no SmartPlayer.

Consulta as duas plataformas ao vivo e cruza com o manifest. Um vídeo é considerado
coberto se está no manifest OU se já existe uma mídia de mesmo nome no SP (casos
subidos manualmente, fora do pipeline).

Uso:
    python scripts/check_gap_panda_sp.py            # relatório de cobertura
    python scripts/check_gap_panda_sp.py --check    # self-check do matcher de títulos
"""
from __future__ import annotations

import asyncio
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from src.config import Settings
from src.manifest import Manifest
from src.panda_client import PandaClient
from src.smartplayer_client import SmartPlayerClient

MANIFEST_PATH = Path("data/manifest.json")
TOKEN_CACHE = Path("data/.sp_token_cache.json")


# ----------------------------------------------------------------------------
# Matcher de títulos
# ----------------------------------------------------------------------------
def _strip(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\.(mp4|mov|mkv|avi|webm)$", "", s.strip())


def norm_full(s: str) -> str:
    """Chave estrita: remove acento, extensão e pontuação."""
    return re.sub(r"[^a-z0-9]+", "", _strip(s))


def norm_body(s: str) -> str:
    """Chave tolerante: remove também o prefixo de numeração.

    O Panda usa "2 - Título" e o SP costuma ter "02 Título" para a mesma aula.
    Sem esta chave, a aula aparece como faltante e é remigrada em duplicata.
    """
    # sem \s* entre \d+ e [a-z]: "02 Como" perderia o "c" e deixaria de casar com "2 - Como"
    s = re.sub(r"^\s*\d+[a-z]?\s*[-._)]*\s*", "", _strip(s))
    return re.sub(r"[^a-z0-9]+", "", s)


def _self_check() -> None:
    # mesma aula, numeração reescrita pelo SP
    assert norm_body("2 - Como aproveitar melhor o curso.mp4") == norm_body("02 Como aproveitar melhor o curso")
    assert norm_body("15B - Introdução corrigida.mp4") == norm_body("15b Introducao corrigida")
    assert norm_body("9 - Comparativo de planos (avançado).mp4") == norm_body("09 Comparativo de planos avancado")
    # a numeração não pode comer a primeira letra do título
    assert norm_body("02 Como aproveitar") == "comoaproveitar"
    assert norm_body("1 - Introdução") == "introducao"
    # aulas diferentes continuam diferentes
    assert norm_body("3 - Instalando ChatGPT") != norm_body("4 - Uma breve história")
    # chave estrita não ignora a numeração
    assert norm_full("2 - Tags.mp4") != norm_full("5 - Tags.mp4")
    assert norm_full("Sistema EI - 04.mkv") == norm_full("sistema ei  04")
    print("self-check do matcher: OK")


# ----------------------------------------------------------------------------
# Coleta
# ----------------------------------------------------------------------------
async def _walk(panda: PandaClient, folder_id: str, path: str, out: list, depth: int = 0) -> None:
    for v in await panda.list_videos(folder_id):
        out.append({
            "id": v.id,
            "title": v.title,
            "status": (v.status or "").upper(),
            "folder_path": path,
            "size": getattr(v, "storage_size", None) or getattr(v, "size", None) or 0,
        })
    if depth < 4:
        for sub in await panda.list_folders(parent_folder_id=folder_id):
            await _walk(panda, sub.id, f"{path}/{sub.name}", out, depth + 1)


async def collect_panda(settings: Settings) -> list[dict]:
    """Inventário do Panda, deduplicado por id.

    list_folders() sem parent devolve a hierarquia ACHATADA (subpastas incluídas),
    então a travessia visita o mesmo vídeo por vários caminhos. Fica o caminho mais
    longo, que é o mais específico.
    """
    raw: list[dict] = []
    async with PandaClient(settings.panda_api_key) as panda:
        for root in await panda.list_folders():
            await _walk(panda, root.id, root.name, raw)
    best: dict[str, dict] = {}
    for v in raw:
        cur = best.get(v["id"])
        if cur is None or len(v["folder_path"]) > len(cur["folder_path"]):
            best[v["id"]] = v
    return list(best.values())


async def collect_sp(settings: Settings) -> list[dict]:
    """Mídias do SmartPlayer, paginando /medias/names.

    ATENÇÃO: /medias/names OMITE mídias em PARTIAL_COMPLETED. Um vídeo que exista
    no SP nesse estado e não esteja no manifest aparece aqui como gap (falso
    positivo). Confirme com GET /medias/{code} ou pelo painel antes de remigrar.
    """
    items: list[dict] = []
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
                items += batch
                if len(batch) < 200:
                    break
                page += 1
    return items


# ----------------------------------------------------------------------------
async def main() -> None:
    settings = Settings.from_env()
    manifest = Manifest.load(MANIFEST_PATH)
    migrated = set(manifest.videos)

    print("Consultando Panda...")
    panda_videos = await collect_panda(settings)
    print("Consultando SmartPlayer...")
    sp_medias = await collect_sp(settings)

    sp_full = {norm_full(m["name"]) for m in sp_medias}
    sp_body = {norm_body(m["name"]) for m in sp_medias}

    converted = [v for v in panda_videos if v["status"] == "CONVERTED"]
    fora = [v for v in converted if v["id"] not in migrated]
    ja_no_sp = [v for v in fora
                if norm_full(v["title"]) in sp_full or norm_body(v["title"]) in sp_body]
    gap = [v for v in fora if v not in ja_no_sp]

    gb = sum(v["size"] for v in gap) / 1024 ** 3
    print()
    print(f"Panda CONVERTED (únicos):   {len(converted)}")
    print(f"Mídias no SmartPlayer:      {len(sp_medias)}")
    print(f"No manifest:                {len(migrated)}")
    print(f"Fora do manifest:           {len(fora)}")
    print(f"  já presentes no SP:       {len(ja_no_sp)}  (match por título)")
    print(f"  GAP REAL:                 {len(gap)}  ({gb:.1f} GB)")

    if not gap:
        print("\nCobertura completa — nada a migrar.")
        return

    per = collections.defaultdict(list)
    for v in gap:
        per[v["folder_path"]].append(v)
    print("\n" + "=" * 88)
    for path in sorted(per):
        vs = per[path]
        print(f"{len(vs):>4} vid | {sum(x['size'] for x in vs)/1024**3:7.2f} GB | {path}")
    print("=" * 88)
    print("\nPastas raiz para passar ao discover --folder-names:")
    for r in sorted({v["folder_path"].split("/")[0] for v in gap}):
        print(f'  "{r}"')


if __name__ == "__main__":
    if "--check" in sys.argv:
        _self_check()
    else:
        asyncio.run(main())
