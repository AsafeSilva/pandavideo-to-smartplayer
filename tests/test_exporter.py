import csv
from pathlib import Path

import pytest

from src.exporter import export_markdown, export_csv
from src.manifest import Manifest
from src.models import VideoEntry, VideoState


def _seed(tmp_path: Path) -> Manifest:
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(
        panda_id="v1", panda_folder="EDU | LIVES", title="Aula 01",
        size_bytes=10 * 1024 * 1024, duration_sec=120,
        state=VideoState.DONE, sp_media_code="m1",
        sp_embed_url="https://player.scaleup.com.br/embed/m1",
        thumbnail_url="https://t/v1.jpg",
    ))
    m.upsert_video(VideoEntry(
        panda_id="v2", panda_folder="EDU | LIVES", title="Aula 02",
        size_bytes=5 * 1024 * 1024, duration_sec=60,
        state=VideoState.FAILED, last_error="upload timeout",
    ))
    return m


def test_export_markdown_groups_by_folder(tmp_path: Path):
    m = _seed(tmp_path)
    out = tmp_path / "log.md"
    export_markdown(m, out)
    content = out.read_text(encoding="utf-8")
    assert "## EDU | LIVES" in content
    assert "Aula 01" in content
    assert "https://player.scaleup.com.br/embed/m1" in content
    assert "upload timeout" in content


def test_export_csv_columns_and_rows(tmp_path: Path):
    m = _seed(tmp_path)
    out = tmp_path / "log.csv"
    export_csv(m, out)
    with out.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
    assert {r["panda_id"] for r in rows} == {"v1", "v2"}
    expected_cols = {"pasta", "titulo", "panda_id", "panda_url", "panda_thumbnail",
                     "duracao_segundos", "size_mb", "sp_media_code", "sp_embed_url",
                     "status", "erro", "executado_em"}
    assert expected_cols.issubset(set(rows[0].keys()))
