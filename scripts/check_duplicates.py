"""
Verifica mídias duplicadas no SmartPlayer.

Uso:
    python check_duplicates.py [--status STATUS] [--out duplicates.csv]

Pagina GET /medias/names completamente, agrupa por nome normalizado,
lista duplicatas e estima o espaço que seria liberado removendo-as.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, ".")
from src.config import Settings
from src.smartplayer_client import SmartPlayerClient

PAGE_SIZE = 100


async def fetch_all_medias(
    sp: SmartPlayerClient,
    status: Optional[str] = None,
    text: str = "",
) -> list[dict]:
    """Pagina GET /medias/names até esgotar resultados."""
    all_medias: list[dict] = []
    page = 0

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), http2=True) as c:
        while True:
            params: dict = {"page": page, "size": PAGE_SIZE}
            if text:
                params["text"] = text
            if status:
                params["status"] = status

            headers = await sp._authed_headers()
            r = await c.get(f"{sp._base_url}/medias/names", headers=headers, params=params)
            r.raise_for_status()
            data = r.json()

            content = data.get("content", [])
            total_pages = data.get("totalPages", 1)
            total_elements = data.get("totalElements", "?")

            all_medias.extend(content)
            print(
                f"  página {page + 1}/{total_pages}: {len(content)} mídias"
                f"  (acumulado: {len(all_medias)}/{total_elements})",
                flush=True,
            )

            if data.get("last", True) or not content:
                break
            page += 1

    return all_medias


def normalize_title(title: str) -> str:
    """Normaliza título para comparação: strip + lower + colapsa espaços."""
    return " ".join(title.lower().split())


def analyze_duplicates(medias: list[dict]) -> dict[str, list[dict]]:
    """Retorna apenas grupos com 2+ mídias pelo nome normalizado."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for m in medias:
        key = normalize_title(m.get("name") or "")
        if key:
            groups[key].append(m)
    return {k: v for k, v in groups.items() if len(v) > 1}


def gb(size_mb: float) -> float:
    """Converte MB para GB (o campo 'size' da API vem em MB)."""
    return size_mb / 1024


def print_report(duplicates: dict[str, list[dict]], total_count: int) -> None:
    if not duplicates:
        print(f"\n✓ Nenhuma duplicata encontrada entre {total_count} mídias.")
        return

    dup_count = sum(len(v) - 1 for v in duplicates.values())
    print(f"\n{'=' * 72}")
    print(f"DUPLICATAS — {len(duplicates)} grupos, {dup_count} cópia(s) extra(s)")
    print(f"(total de mídias analisadas: {total_count})")
    print(f"{'=' * 72}\n")

    total_extra_gb = 0.0
    for _key, group in sorted(duplicates.items(), key=lambda x: -len(x[1])):
        print(f"  Nome: {group[0].get('name')}")
        sizes_mb = []
        for m in group:
            size_mb = m.get("size") or 0.0
            sizes_mb.append(size_mb)
            print(
                f"    code={m.get('code', '?')}  "
                f"status={m.get('status', '?'):12}  "
                f"size={gb(size_mb):.2f}GB  "
                f"duration={m.get('durationValue', '?')}"
            )
        # Espaço extra = tudo além da maior cópia
        extra_gb = gb(sum(sizes_mb) - max(sizes_mb))
        total_extra_gb += extra_gb
        print(f"    -> {len(group) - 1} copia(s) extra(s), ~{extra_gb:.2f}GB potencialmente liberaveis\n")

    print(f"{'=' * 72}")
    print(f"Espaço total potencialmente liberável: ~{total_extra_gb:.2f} GB")
    print(f"{'=' * 72}\n")


def write_csv(duplicates: dict[str, list[dict]], path: Path) -> None:
    rows = []
    for group in duplicates.values():
        for m in group:
            rows.append({
                "name": m.get("name", ""),
                "code": m.get("code", ""),
                "status": m.get("status", ""),
                "size_gb": f"{gb(m.get('size') or 0):.3f}",
                "duration": m.get("durationValue", ""),
                "resolution": m.get("resolution", ""),
            })
    rows.sort(key=lambda r: (normalize_title(r["name"]), r["code"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV salvo em: {path}")


async def main(status: Optional[str], out: str) -> None:
    settings = Settings.from_env()
    sp = SmartPlayerClient(
        client_id=settings.sp_client_id,
        client_secret=settings.sp_client_secret,
        user_code=settings.sp_user_code,
        token_cache_path=Path("data/.sp_token_cache.json"),
    )

    async with sp:
        print(f"Buscando todas as mídias via GET /medias/names (pageSize={PAGE_SIZE})...")
        if status:
            print(f"  filtro: status={status}")
        medias = await fetch_all_medias(sp, status=status)

    print(f"\nTotal de mídias obtidas: {len(medias)}")

    duplicates = analyze_duplicates(medias)
    print_report(duplicates, len(medias))

    if out and duplicates:
        write_csv(duplicates, Path(out))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verifica duplicatas no SmartPlayer")
    parser.add_argument(
        "--status",
        default=None,
        help="Filtrar por status (COMPLETED, DRAFT, ERROR…). Padrão: todos.",
    )
    parser.add_argument(
        "--out",
        default="duplicates.csv",
        help="Arquivo CSV de saída (padrão: duplicates.csv)",
    )
    args = parser.parse_args()
    asyncio.run(main(status=args.status, out=args.out))
