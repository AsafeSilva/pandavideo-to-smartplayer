from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.migrate import build_parser, cmd_discover, cmd_run, cmd_retry_failed, cmd_export, cmd_cleanup
from src.manifest import Manifest


@pytest.mark.asyncio
async def test_cmd_discover_invokes_discovery(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PANDA_API_KEY", "k")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "x")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "x")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "x")

    manifest_path = tmp_path / "manifest.json"

    with patch("src.migrate.PandaClient") as PandaCls, \
         patch("src.migrate.discover", new_callable=AsyncMock) as disc:
        instance = PandaCls.return_value
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = False

        args = build_parser().parse_args(["discover", "--prefix", "EDU |"])
        await cmd_discover(args, manifest_path=manifest_path)

        disc.assert_awaited_once()


def test_parser_has_subcommands():
    p = build_parser()
    args = p.parse_args(["run", "--dry-run"])
    assert args.command == "run"
    assert args.dry_run is True


@pytest.mark.asyncio
async def test_cmd_run_dry_run_prints_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PANDA_API_KEY", "k")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "x")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "x")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "x")

    from src.manifest import Manifest
    from src.models import VideoEntry
    m = Manifest.load(tmp_path / "manifest.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T1",
                              size_bytes=100_000_000))
    m.upsert_video(VideoEntry(panda_id="v2", panda_folder="F", title="T2",
                              size_bytes=200_000_000))
    m.save()

    args = build_parser().parse_args(["run", "--dry-run"])
    await cmd_run(args, manifest_path=tmp_path / "manifest.json",
                  download_dir=tmp_path / "downloads")

    out = capsys.readouterr().out
    assert "Plano de migração" in out
    assert "2" in out


@pytest.mark.asyncio
async def test_cmd_retry_failed_resets_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PANDA_API_KEY", "k")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "x")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "x")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "x")

    from src.manifest import Manifest
    from src.models import VideoEntry, VideoState

    m = Manifest.load(tmp_path / "manifest.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T",
                              state=VideoState.FAILED, last_error="x"))
    m.save()

    args = build_parser().parse_args(["retry-failed"])
    await cmd_retry_failed(args, manifest_path=tmp_path / "manifest.json",
                           token_cache=tmp_path / "tok.json",
                           download_dir=tmp_path / "dl",
                           run_pipeline_fn=lambda **kw: None)

    from src.manifest import Manifest as M2
    reloaded = M2.load(tmp_path / "manifest.json")
    assert reloaded.videos["v1"].state == VideoState.PENDING
    assert reloaded.videos["v1"].last_error is None


def test_cmd_export_writes_files(tmp_path):
    from src.manifest import Manifest
    from src.models import VideoEntry

    m = Manifest.load(tmp_path / "manifest.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T"))
    m.save()

    md = tmp_path / "log.md"
    csv_p = tmp_path / "log.csv"
    cmd_export(manifest_path=tmp_path / "manifest.json", md_path=md, csv_path=csv_p)
    assert md.exists() and csv_p.exists()


def test_cmd_cleanup_removes_done_files(tmp_path):
    from src.manifest import Manifest
    from src.models import VideoEntry, VideoState

    f = tmp_path / "v1.mp4"
    f.write_bytes(b"x")
    m = Manifest.load(tmp_path / "manifest.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T",
                              state=VideoState.DONE, local_video_path=str(f)))
    m.save()

    cmd_cleanup(manifest_path=tmp_path / "manifest.json")
    assert not f.exists()
