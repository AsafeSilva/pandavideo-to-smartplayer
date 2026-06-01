"""
Diagnóstico de armazenamento do Panda Video.

Uso:
    python check_panda_storage.py [--out panda_videos.csv]

Lista TODOS os vídeos (sem filtro de pasta), soma storage_size,
agrupa por status e mostra os maiores arquivos.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, ".")
from src.config import Settings

PANDA_BASE_URL = "https://api-v2.pandavideo.com.br"
PAGE_SIZE = 100


def gb(size_bytes: float) -> float:
    return size_bytes / (1024 ** 3)


async def fetch_all_panda_videos(api_key: str) -> list[dict]:
    all_videos: list[dict] = []
    page = 1

    async with httpx.AsyncClient(
        headers={"Authorization": api_key, "Accept": "application/json"},
        timeout=httpx.Timeout(30.0, connect=10.0),
        http2=True,
    ) as c:
        while True:
            r = await c.get(
                f"{PANDA_BASE_URL}/videos",
                params={"page": page, "limit": PAGE_SIZE},
            )
            r.raise_for_status()
            data = r.json()
            videos = data.get("videos", []) if isinstance(data, dict) else data

            if not videos:
                break

            all_videos.extend(videos)
            print(f"  pagina {page}: {len(videos)} videos (acumulado: {len(all_videos)})", flush=True)

            if len(videos) < PAGE_SIZE:
                break
            page += 1

    return all_videos


def print_report(videos: list[dict]) -> None:
    total_bytes = sum(v.get("storage_size") or 0 for v in videos)
    converted_bytes = sum(
        v.get("storage_size") or 0 for v in videos if v.get("status") == "CONVERTED"
    )

    print(f"\n{'=' * 65}")
    print(f"PANDA VIDEO — DIAGNOSTICO DE ARMAZENAMENTO")
    print(f"{'=' * 65}")
    print(f"Total de videos:        {len(videos)}")
    print(f"Armazenamento total:    {gb(total_bytes):.2f} GB")
    print(f"  (so CONVERTED):       {gb(converted_bytes):.2f} GB")

    # Por status
    by_status: dict[str, list[dict]] = defaultdict(list)
    for v in videos:
        by_status[v.get("status", "?")].append(v)

    print(f"\nPor status:")
    for status, group in sorted(by_status.items()):
        size = sum(v.get("storage_size") or 0 for v in group)
        print(f"  {status:<15} {len(group):>5} videos   {gb(size):>8.2f} GB")

    # Top 20 maiores
    top = sorted(videos, key=lambda v: v.get("storage_size") or 0, reverse=True)[:20]
    print(f"\nTop 20 maiores videos:")
    for i, v in enumerate(top, 1):
        size = v.get("storage_size") or 0
        title = (v.get("title") or "?")[:60]
        status = v.get("status", "?")
        length = v.get("length") or 0
        dur = f"{int(length)//3600:02}:{(int(length)%3600)//60:02}:{int(length)%60:02}"
        print(f"  {i:>2}. {gb(size):>6.2f}GB  [{status}]  {dur}  {title}")

    print(f"\n{'=' * 65}")
    print(f"Comparativo estimado:")
    print(f"  Panda:       {len(videos):>4} videos  {gb(total_bytes):>8.2f} GB")
    print(f"  SmartPlayer: {432:>4} videos  {858.73:>8.2f} GB (conforme painel)")
    if len(videos) > 0 and total_bytes > 0:
        avg_sp = 858730 / 432  # MB por video no SP
        avg_panda = gb(total_bytes) * 1024 / len(videos)  # MB por video no Panda
        print(f"  Media por video — Panda: {avg_panda:.0f}MB   SP: {avg_sp:.0f}MB")
    print(f"{'=' * 65}\n")


def write_csv(videos: list[dict], path: Path) -> None:
    rows = []
    for v in videos:
        rows.append({
            "id": v.get("id", ""),
            "title": (v.get("title") or "")[:120],
            "status": v.get("status", ""),
            "folder_id": v.get("folder_id") or "",
            "storage_size_gb": f"{gb(v.get('storage_size') or 0):.3f}",
            "length_sec": v.get("length") or 0,
            "created_at": (v.get("created_at") or "")[:19],
        })
    rows.sort(key=lambda r: r["storage_size_gb"], reverse=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV salvo em: {path}")


async def main(out: str) -> None:
    settings = Settings.from_env()

    print(f"Buscando todos os videos do Panda Video (pageSize={PAGE_SIZE})...")
    videos = await fetch_all_panda_videos(settings.panda_api_key)

    print(f"\nTotal obtido: {len(videos)} videos")
    print_report(videos)

    if out and videos:
        write_csv(videos, Path(out))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnostico de armazenamento Panda Video")
    parser.add_argument("--out", default="panda_videos.csv", help="CSV de saida (padrao: panda_videos.csv)")
    args = parser.parse_args()
    asyncio.run(main(out=args.out))
