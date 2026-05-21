"""Geração de output tabelado (Markdown agrupado + CSV plano)."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from src.manifest import Manifest
from src.models import VideoEntry, VideoState


PANDA_VIDEO_URL_TEMPLATE = "https://app.pandavideo.com/videos/{id}"


def _size_mb(v: VideoEntry) -> str:
    return f"{v.size_bytes / (1024 * 1024):.1f}"


def _panda_url(v: VideoEntry) -> str:
    return PANDA_VIDEO_URL_TEMPLATE.format(id=v.panda_id)


def export_markdown(manifest: Manifest, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = len(manifest.videos)
    done = sum(1 for v in manifest.videos.values() if v.state == VideoState.DONE)
    failed = sum(1 for v in manifest.videos.values() if v.state == VideoState.FAILED)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append("# Migração Panda Video → SmartPlayer\n")
    lines.append(f"Executada em: {now}\n")
    lines.append(f"Vídeos descobertos: {total} | Sucesso: {done} | Falhas: {failed}\n")

    by_folder: dict[str, list[VideoEntry]] = {}
    for v in manifest.videos.values():
        by_folder.setdefault(v.panda_folder, []).append(v)

    for folder, videos in sorted(by_folder.items()):
        lines.append(f"\n## {folder} ({len(videos)} vídeos)\n")
        lines.append("| # | Título | Panda ID | Panda URL | SP Code | Embed URL | Status |")
        lines.append("|---|--------|----------|-----------|---------|-----------|--------|")
        for i, v in enumerate(sorted(videos, key=lambda x: x.title), start=1):
            status_text = v.state.value
            if v.state == VideoState.FAILED and v.last_error:
                status_text = f"failed: {v.last_error}"
            sp_code = v.sp_media_code or "—"
            embed = v.sp_embed_url or "—"
            lines.append(
                f"| {i} | {v.title} | {v.panda_id} | {_panda_url(v)} | {sp_code} | {embed} | {status_text} |"
            )

    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


CSV_COLUMNS = [
    "pasta", "titulo", "panda_id", "panda_url", "panda_thumbnail",
    "duracao_segundos", "size_mb", "sp_media_code", "sp_embed_url",
    "status", "erro", "executado_em",
]


def export_csv(manifest: Manifest, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with dest.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for v in manifest.videos.values():
            w.writerow({
                "pasta": v.panda_folder,
                "titulo": v.title,
                "panda_id": v.panda_id,
                "panda_url": _panda_url(v),
                "panda_thumbnail": v.thumbnail_url or "",
                "duracao_segundos": v.duration_sec,
                "size_mb": _size_mb(v),
                "sp_media_code": v.sp_media_code or "",
                "sp_embed_url": v.sp_embed_url or "",
                "status": v.state.value,
                "erro": v.last_error or "",
                "executado_em": now,
            })
