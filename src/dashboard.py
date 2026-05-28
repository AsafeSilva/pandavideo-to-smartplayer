"""Dashboard live no terminal usando rich.Live."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.models import VideoState
from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from src.manifest import Manifest

_PHASE_ICON = {
    "requisitando": "[cyan]↺[/]",
    "ag. Panda...": "[yellow]⌛[/]",
    "baixando...": "[cyan]⬇[/]",
    "concluído": "[green]✓[/]",
    "criando media": "[cyan]↺[/]",
    "enviando": "[cyan]↑[/]",
    "encoding...": "[yellow]⚙[/]",
    "DONE": "[green]✓[/]",
}


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class LiveDashboard:
    def __init__(self, manifest: Manifest, max_dl_workers: int, max_up_workers: int) -> None:
        self._manifest = manifest
        self._max_dl = max_dl_workers
        self._max_up = max_up_workers
        self._active_downloads: dict[str, dict] = {}
        self._active_uploads: dict[str, dict] = {}
        self._pipeline_start = time.monotonic()
        self._live = Live(
            self._build_renderable(),
            refresh_per_second=4,
            screen=False,
            transient=False,
        )

    def __enter__(self) -> "LiveDashboard":
        self._live.__enter__()
        return self

    def __exit__(self, *args) -> None:
        self._live.update(self._build_renderable())
        self._live.__exit__(*args)

    # ── callbacks chamados pelo pipeline ──────────────────────────────────

    def on_download_start(self, vid_id: str, title: str, size_mb: float = 0) -> None:
        self._active_downloads[vid_id] = {
            "title": title,
            "phase": "requisitando",
            "size_mb": size_mb,
            "started_at": time.monotonic(),
        }
        self._refresh()

    def on_download_phase(self, vid_id: str, phase: str) -> None:
        if vid_id in self._active_downloads:
            self._active_downloads[vid_id]["phase"] = phase
            self._refresh()

    def on_download_done(self, vid_id: str) -> None:
        self._active_downloads.pop(vid_id, None)
        self._refresh()

    def on_upload_start(self, vid_id: str, title: str) -> None:
        self._active_uploads[vid_id] = {
            "title": title,
            "phase": "criando media",
            "started_at": time.monotonic(),
        }
        self._refresh()

    def on_upload_phase(self, vid_id: str, phase: str) -> None:
        if vid_id in self._active_uploads:
            self._active_uploads[vid_id]["phase"] = phase
            self._refresh()

    def on_upload_done(self, vid_id: str) -> None:
        self._active_uploads.pop(vid_id, None)
        self._refresh()

    # ── renderização ──────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._live.update(self._build_renderable())

    def _build_renderable(self) -> Group:
        elapsed = time.monotonic() - self._pipeline_start
        counts: dict[str, int] = {}
        for v in self._manifest.videos.values():
            counts[v.state.value] = counts.get(v.state.value, 0) + 1
        total = len(self._manifest.videos)
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)

        # ── cabeçalho ─────────────────────────────────────────────────────
        pct = done / total if total else 0
        bar_width = 28
        filled = int(bar_width * pct)
        bar = "[bold green]" + "█" * filled + "[/][dim]" + "░" * (bar_width - filled) + "[/]"
        fail_str = f"[bold red]✗ {failed} falha{'s' if failed != 1 else ''}[/]" if failed else "[dim]✗ 0 falhas[/]"
        header_text = (
            f"{bar}  [bold]{done}/{total}[/] concluídos  {fail_str}  "
            f"[dim]{_fmt_elapsed(elapsed)}[/]"
        )
        header = Panel(Text.from_markup(header_text), title="[bold]Migração Panda → SmartPlayer[/]", box=box.ROUNDED)

        # ── tabela de downloads ────────────────────────────────────────────
        dl_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=True)
        dl_table.add_column("icon", width=3, no_wrap=True)
        dl_table.add_column("title", ratio=1, no_wrap=True)
        dl_table.add_column("phase", width=16, no_wrap=True)
        dl_table.add_column("elapsed", width=6, no_wrap=True, justify="right")

        active_dl = list(self._active_downloads.items())
        for vid_id, slot in active_dl[: self._max_dl]:
            icon = _PHASE_ICON.get(slot["phase"], "[cyan]⬇[/]")
            el = _fmt_elapsed(time.monotonic() - slot["started_at"])
            title = slot["title"][:52] + "…" if len(slot["title"]) > 53 else slot["title"]
            dl_table.add_row(icon, title, f"[dim]{slot['phase']}[/]", f"[dim]{el}[/]")

        for _ in range(self._max_dl - len(active_dl)):
            dl_table.add_row("[dim]·[/]", "[dim](livre)[/]", "", "")

        dl_panel = Panel(dl_table, title="[bold cyan]Downloads[/]", box=box.ROUNDED)

        # ── tabela de uploads ──────────────────────────────────────────────
        up_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=True)
        up_table.add_column("icon", width=3, no_wrap=True)
        up_table.add_column("title", ratio=1, no_wrap=True)
        up_table.add_column("phase", width=16, no_wrap=True)
        up_table.add_column("elapsed", width=6, no_wrap=True, justify="right")

        active_up = list(self._active_uploads.items())
        for vid_id, slot in active_up[: self._max_up]:
            icon = _PHASE_ICON.get(slot["phase"], "[cyan]↑[/]")
            el = _fmt_elapsed(time.monotonic() - slot["started_at"])
            title = slot["title"][:52] + "…" if len(slot["title"]) > 53 else slot["title"]
            up_table.add_row(icon, title, f"[dim]{slot['phase']}[/]", f"[dim]{el}[/]")

        for _ in range(self._max_up - len(active_up)):
            up_table.add_row("[dim]·[/]", "[dim](livre)[/]", "", "")

        up_panel = Panel(up_table, title="[bold green]Uploads / Encoding[/]", box=box.ROUNDED)

        # ── resumo de estados ──────────────────────────────────────────────
        parts = []
        for state in VideoState:
            n = counts.get(state.value, 0)
            if n:
                label = state.value.replace("sp_", "").replace("download_", "dl_").replace("_", " ").upper()
                if state == VideoState.DONE:
                    parts.append(f"[bold green]DONE:{n}[/]")
                elif state == VideoState.FAILED:
                    parts.append(f"[bold red]FAILED:{n}[/]")
                else:
                    parts.append(f"[dim]{label}:{n}[/]")
        summary = Panel(
            Text.from_markup("  ".join(parts) or "[dim]sem vídeos[/]"),
            title="[bold]Resumo[/]",
            box=box.ROUNDED,
        )

        return Group(header, dl_panel, up_panel, summary)
