"""
Calcula o tamanho total de vídeos em grupos de pastas do Panda.

Uso:
    python scripts/check_folders_size.py
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from src.config import Settings
from src.panda_client import PandaClient


def gb(size_bytes: float) -> str:
    return f"{size_bytes / (1024 ** 3):.2f} GB"


async def collect_folder_videos(panda: PandaClient, folder_id: str, depth: int = 0) -> list[dict]:
    """Coleta vídeos de uma pasta e suas subpastas recursivamente."""
    videos = await panda.list_videos(folder_id)
    converted = [
        {"title": v.title, "size": v.storage_size or v.size or 0}
        for v in videos
        if v.status.upper() == "CONVERTED"
    ]
    if not converted and depth < 2:
        subfolders = await panda.list_folders(parent_folder_id=folder_id)
        for sub in subfolders:
            converted.extend(await collect_folder_videos(panda, sub.id, depth + 1))
    return converted


async def main() -> None:
    settings = Settings.from_env()

    # Grupos a verificar: (nome_grupo, lista de (nome_pasta, folder_id))
    GROUPS = [
        ("EQUIPE |", [
            ("EQUIPE I All Hands 2024",            "98a79703-cc55-4b9a-b06f-f68c4ce2cdb6"),
            ("EQUIPE I All Hands Anual 2024",       "703f02a1-724f-4e9f-b876-011cae53afb9"),
            ("EQUIPE | All Hands Fev/2025",         "87028dae-e1f1-4319-851a-47301011393c"),
            ("EQUIPE | All Hands Jan/2025",         "b97c2627-5884-4b3a-9099-064e7eec1529"),
            ("EQUIPE | Automações",               "27d2c4ea-f7c2-49cc-b029-f9c2d523786a"),
            ("EQUIPE | Planejamento EI | 2023",     "15970fee-8cff-4a9d-9c3a-78cc36fd107d"),
            ("EQUIPE | Planejamento EI | 2024",     "a0b33922-d325-4a9d-b818-062ddcb27fa2"),
            ("EQUIPE | Planejamento EI | 2025",     "3a9dd2df-65f7-4116-90d7-818551ac7e42"),
            ("EQUIPE | Planejamento Empresarial",   "0d76c9f3-5ca0-40f3-bad5-57d97d5a196a"),
            ("EQUIPE | Processos",                  "ce2723e7-2ef7-4570-854e-bd73d0d390da"),
            ("EQUIPE | Produtos",                   "1a9c1ce1-865b-49de-ab51-7eacdf98e8e4"),
            ("EQUIPE | Treinamento de Ferramentas", "910a0524-eb38-47c4-a2c5-6b0d2b14bb52"),
            ("EQUIPE | Treinamentos Internos",      "1ca5ac23-86a2-4685-8e38-2e50149f8349"),
        ]),
        ("MARKETING |", [
            ("MARKETING",                              "bc924e77-f606-419a-bb36-1d7a12cfca5f"),
            ("MARKETING | Depoimentos",                "024070b8-ef98-45ea-a097-bb8654f7d7a8"),
            ("MARKETING | Lives, Workshops e Tutorias","3df5a6d1-7414-4ffe-8f71-832fc6061ca9"),
        ]),
        ("SAAS", [
            ("SAAS | Treinamentos", "e46da799-61f3-4153-8efa-8384f3798113"),
        ]),
        ("SDR", [
            ("Treinamento técnico do produto SDR", "bf2bd0eb-bba2-4848-8342-b7d8d7cd3b5e"),
        ]),
    ]

    print("Consultando Panda Video...\n")

    grand_total_bytes = 0.0
    grand_total_videos = 0

    async with PandaClient(settings.panda_api_key) as panda:
        for group_name, folders in GROUPS:
            group_bytes = 0.0
            group_videos = 0
            folder_lines = []

            for folder_name, folder_id in folders:
                videos = await collect_folder_videos(panda, folder_id)
                size = sum(v["size"] for v in videos)
                group_bytes += size
                group_videos += len(videos)
                if videos:
                    folder_lines.append(f"    {folder_name:<45} {len(videos):>3} vídeos  {gb(size)}")
                else:
                    folder_lines.append(f"    {folder_name:<45}   0 vídeos  0.00 GB")

            print(f"{'=' * 65}")
            print(f"  {group_name}")
            for line in folder_lines:
                print(line)
            print(f"  SUBTOTAL: {group_videos} vídeos  {gb(group_bytes)}")

            grand_total_bytes += group_bytes
            grand_total_videos += group_videos

    print(f"\n{'=' * 65}")
    print(f"  TOTAL GERAL: {grand_total_videos} vídeos  {gb(grand_total_bytes)}")
    print(f"  Limite disponível no SmartPlayer: ~800 GB")
    remaining = 800 - grand_total_bytes / (1024 ** 3)
    if remaining >= 0:
        print(f"  Saldo após migração: ~{remaining:.1f} GB")
    else:
        print(f"  ATENÇÃO: excede em ~{-remaining:.1f} GB!")
    print(f"{'=' * 65}\n")


if __name__ == "__main__":
    asyncio.run(main())
