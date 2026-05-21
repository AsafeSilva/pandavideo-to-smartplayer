"""State machine persistente em JSON, com escrita atômica."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from src.models import FolderEntry, VideoEntry, VideoState, _utcnow


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.folders: dict[str, FolderEntry] = {}
        self.videos: dict[str, VideoEntry] = {}
        self.discovered_at: Optional[str] = None

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        m = cls(path)
        if not path.exists():
            return m
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        m.discovered_at = data.get("discovered_at")
        for name, raw in data.get("folders", {}).items():
            m.folders[name] = FolderEntry(**raw)
        for vid, raw in data.get("videos", {}).items():
            raw["state"] = VideoState(raw["state"])
            m.videos[vid] = VideoEntry(**raw)
        return m

    def save(self) -> None:
        payload = {
            "discovered_at": self.discovered_at,
            "folders": {name: asdict(f) for name, f in self.folders.items()},
            "videos": {
                vid: {**asdict(v), "state": v.state.value}
                for vid, v in self.videos.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def upsert_folder(self, name: str, entry: FolderEntry) -> None:
        self.folders[name] = entry

    def upsert_video(self, v: VideoEntry) -> None:
        self.videos[v.panda_id] = v

    def transition(self, panda_id: str, new_state: VideoState, **fields) -> VideoEntry:
        v = self.videos[panda_id]
        v.state = new_state
        for k, val in fields.items():
            setattr(v, k, val)
        v.updated_at = _utcnow()
        self.save()
        return v

    def videos_in_state(self, *states: VideoState) -> list[VideoEntry]:
        return [v for v in self.videos.values() if v.state in states]

    def mark_failed(self, panda_id: str, error: str) -> VideoEntry:
        v = self.videos[panda_id]
        v.state = VideoState.FAILED
        v.last_error = error
        v.retry_count += 1
        v.updated_at = _utcnow()
        self.save()
        return v
