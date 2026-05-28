"""CLI principal do migrador Panda -> SmartPlayer."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from src.config import Settings
from src.discovery import discover
from src.exporter import export_csv, export_markdown
from src.manifest import Manifest
from src.models import VideoState
from src.panda_client import PandaClient
from src.pipeline import run_pipeline
from src.smartplayer_client import SmartPlayerClient


DEFAULT_MANIFEST = Path("data/manifest.json")
DEFAULT_TOKEN_CACHE = Path("data/token_cache.json")
DEFAULT_DOWNLOAD_DIR = Path("data/downloads")
DEFAULT_LOG_MD = Path("data/migration_log.md")
DEFAULT_LOG_CSV = Path("data/migration_log.csv")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="migrate", description="Panda Video -> SmartPlayer")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list-folders", help="Exibe todas as pastas disponíveis no Panda (ID e nome)")

    pd = sub.add_parser("discover", help="Lista pastas/vídeos do Panda e popula manifest")
    pd.add_argument("--prefix", default="EDUCACIONAL |")
    pd.add_argument(
        "--folder-names",
        nargs="+",
        metavar="NOME",
        default=None,
        help="Nomes exatos das pastas a descobrir (ignora --prefix quando informado)",
    )

    pr = sub.add_parser("run", help="Executa pipeline de migração")
    pr.add_argument("--dry-run", action="store_true")
    pr.add_argument("--resume", action="store_true")
    pr.add_argument("--limit", type=int, default=None, metavar="N",
                    help="Processar apenas os primeiros N vídeos (útil para testes)")

    sub.add_parser("retry-failed", help="Reprocessa vídeos no estado FAILED")
    sub.add_parser("export", help="Gera migration_log.md e .csv a partir do manifest")
    sub.add_parser("cleanup", help="Apaga MP4s locais de vídeos com state=DONE")
    return p


async def cmd_list_folders(manifest_path: Path = DEFAULT_MANIFEST) -> None:
    settings = Settings.from_env()
    async with PandaClient(api_key=settings.panda_api_key) as panda:
        folders = await panda.list_folders()
    if not folders:
        print("Nenhuma pasta encontrada.")
        return
    col_width = max(len(f.name) for f in folders)
    print(f"{'NOME':<{col_width}}  ID")
    print("-" * (col_width + 2 + 36))
    for f in sorted(folders, key=lambda x: x.name):
        print(f"{f.name:<{col_width}}  {f.id}")


async def cmd_discover(args, manifest_path: Path = DEFAULT_MANIFEST) -> None:
    settings = Settings.from_env()
    manifest = Manifest.load(manifest_path)
    folder_names = getattr(args, "folder_names", None)
    async with PandaClient(api_key=settings.panda_api_key) as panda:
        await discover(panda, manifest, prefix=args.prefix, folder_names=folder_names)
    print(f"Discovery completa. {len(manifest.videos)} vídeos em {len(manifest.folders)} pastas.")


def _format_plan(manifest: Manifest) -> str:
    pending = manifest.videos_in_state(
        VideoState.PENDING, VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY,
        VideoState.DOWNLOADED, VideoState.SP_MEDIA_CREATED, VideoState.SP_UPLOAD_URLS_READY,
        VideoState.UPLOADING, VideoState.SP_PROCESSING, VideoState.SP_COMPLETED,
    )
    total_bytes = sum(v.size_bytes for v in pending)
    largest = max((v.size_bytes for v in pending), default=0)
    lines = [
        "Plano de migração",
        f"   Pastas:           {len(manifest.folders)}",
        f"   Vídeos pendentes: {len(pending)}",
        f"   Tamanho total:    {total_bytes / (1024**3):.2f} GB",
        f"   Maior arquivo:    {largest / (1024**3):.2f} GB",
    ]
    return "\n".join(lines)


async def cmd_run(
    args,
    manifest_path: Path = DEFAULT_MANIFEST,
    token_cache: Path = DEFAULT_TOKEN_CACHE,
    download_dir: Path = DEFAULT_DOWNLOAD_DIR,
) -> None:
    settings = Settings.from_env()
    manifest = Manifest.load(manifest_path)

    if not manifest.videos:
        print("Manifest vazio. Rode `discover` primeiro.")
        return

    print(_format_plan(manifest))

    if args.dry_run:
        return

    async with PandaClient(api_key=settings.panda_api_key) as panda, \
               SmartPlayerClient(
                   client_id=settings.sp_client_id,
                   client_secret=settings.sp_client_secret,
                   user_code=settings.sp_user_code,
                   token_cache_path=token_cache,
               ) as sp:
        limit = getattr(args, "limit", None)
        await run_pipeline(
            panda=panda,
            sp=sp,
            manifest=manifest,
            download_dir=download_dir,
            max_download_concurrency=settings.max_download_concurrency,
            max_upload_concurrency=settings.max_upload_concurrency,
            quality=settings.panda_quality,
            limit=limit,
        )

    export_markdown(manifest, DEFAULT_LOG_MD)
    export_csv(manifest, DEFAULT_LOG_CSV)
    print(f"\nLogs gerados em {DEFAULT_LOG_MD} e {DEFAULT_LOG_CSV}")


async def cmd_retry_failed(
    args,
    manifest_path: Path = DEFAULT_MANIFEST,
    token_cache: Path = DEFAULT_TOKEN_CACHE,
    download_dir: Path = DEFAULT_DOWNLOAD_DIR,
    run_pipeline_fn=None,
) -> None:
    settings = Settings.from_env()
    manifest = Manifest.load(manifest_path)
    failed = manifest.videos_in_state(VideoState.FAILED)
    for v in failed:
        v.state = VideoState.PENDING
        v.last_error = None
    manifest.save()
    print(f"Resetados {len(failed)} vídeos para PENDING. Executando pipeline...")

    if run_pipeline_fn is not None:
        run_pipeline_fn()
        return

    async with PandaClient(api_key=settings.panda_api_key) as panda, \
               SmartPlayerClient(
                   client_id=settings.sp_client_id,
                   client_secret=settings.sp_client_secret,
                   user_code=settings.sp_user_code,
                   token_cache_path=token_cache,
               ) as sp:
        await run_pipeline(
            panda=panda, sp=sp, manifest=manifest, download_dir=download_dir,
            max_download_concurrency=settings.max_download_concurrency,
            max_upload_concurrency=settings.max_upload_concurrency,
            quality=settings.panda_quality,
        )


def cmd_export(
    manifest_path: Path = DEFAULT_MANIFEST,
    md_path: Path = DEFAULT_LOG_MD,
    csv_path: Path = DEFAULT_LOG_CSV,
) -> None:
    manifest = Manifest.load(manifest_path)
    export_markdown(manifest, md_path)
    export_csv(manifest, csv_path)
    print(f"Logs gerados: {md_path}, {csv_path}")


def cmd_cleanup(manifest_path: Path = DEFAULT_MANIFEST) -> None:
    manifest = Manifest.load(manifest_path)
    removed = 0
    for v in manifest.videos.values():
        if v.state == VideoState.DONE and v.local_video_path:
            p = Path(v.local_video_path)
            if p.exists():
                p.unlink()
                removed += 1
            v.local_video_path = None
            if v.local_thumb_path:
                pt = Path(v.local_thumb_path)
                if pt.exists():
                    pt.unlink()
                v.local_thumb_path = None
    manifest.save()
    print(f"Arquivos removidos: {removed}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # export and cleanup don't need API credentials
    if args.command == "export":
        cmd_export()
        return 0
    if args.command == "cleanup":
        cmd_cleanup()
        return 0

    # all other commands need settings
    settings = Settings.from_env()
    from datetime import datetime
    from src.logging_setup import configure_logging
    log_path = Path("logs") / f"migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    configure_logging(log_path, level=settings.log_level)

    if args.command == "list-folders":
        asyncio.run(cmd_list_folders())
        return 0
    if args.command == "discover":
        asyncio.run(cmd_discover(args))
        return 0
    if args.command == "run":
        asyncio.run(cmd_run(args))
        return 0
    if args.command == "retry-failed":
        asyncio.run(cmd_retry_failed(args))
        return 0
    print(f"Comando {args.command!r} não reconhecido.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
