# Migração Panda Video → SmartPlayer — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir um script Python CLI que migra vídeos do Panda Video para o SmartPlayer, preservando estrutura de pastas e metadados, com pipeline assíncrono resumível e output tabelado.

**Architecture:** Python 3.11+ com `asyncio` + `httpx`. Pipeline de 2 pools de workers (3 downloaders + 3 uploaders) ligados por fila, estado persistido em `manifest.json` (resumível), retries com `tenacity`, validação com `pydantic`. CLI com `argparse`.

**Tech Stack:** Python 3.11+, httpx[http2], pydantic v2, tenacity, python-dotenv, tqdm, pytest + pytest-asyncio + pytest-httpx (mocks)

**Spec:** `docs/superpowers/specs/2026-05-21-panda-to-smartplayer-migration-design.md`

---

## Task 0: Setup do projeto

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `README.md`
- Create: `src/__init__.py`
- Create: `tests/__init__.py`
- Create: `data/.gitkeep`
- Create: `logs/.gitkeep`

- [ ] **Step 1: Criar venv e estrutura de diretórios**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
mkdir src, tests, data, data\downloads, logs
```

- [ ] **Step 2: Criar `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
data/downloads/
data/manifest.json
data/token_cache.json
data/migration_log.md
data/migration_log.csv
logs/*.log
.DS_Store
```

- [ ] **Step 3: Criar `.env.example`**

```
# Panda Video — obter em Configurações → Avançado → Gerar nova chave API
PANDA_API_KEY=

# SmartPlayer — credenciais OAuth2
SMARTPLAYER_CLIENT_ID=
SMARTPLAYER_CLIENT_SECRET=
SMARTPLAYER_USER_CODE=

# Comportamento
MAX_DOWNLOAD_CONCURRENCY=3
MAX_UPLOAD_CONCURRENCY=3
MAX_DISK_USAGE_GB=10
PANDA_QUALITY=original
LOG_LEVEL=INFO
```

- [ ] **Step 4: Criar `requirements.txt`**

```
httpx[http2]>=0.27,<1.0
python-dotenv>=1.0,<2.0
tqdm>=4.66,<5.0
pydantic>=2.6,<3.0
tenacity>=8.2,<9.0
```

- [ ] **Step 5: Criar `requirements-dev.txt`**

```
-r requirements.txt
pytest>=8.0,<9.0
pytest-asyncio>=0.23,<1.0
pytest-httpx>=0.30,<1.0
```

- [ ] **Step 6: Instalar dependências**

Run: `pip install -r requirements-dev.txt`
Expected: tudo instala sem erro.

- [ ] **Step 7: Criar arquivos init vazios**

Conteúdo de `src/__init__.py` e `tests/__init__.py`: vazio.

Conteúdo de `data/.gitkeep` e `logs/.gitkeep`: vazio.

- [ ] **Step 8: Criar `README.md` mínimo**

```markdown
# Migração Panda Video → SmartPlayer

Script de migração one-shot. Ver spec em `docs/superpowers/specs/2026-05-21-panda-to-smartplayer-migration-design.md`.

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # Windows PowerShell
pip install -r requirements.txt
copy .env.example .env           # depois preencher
```

## Uso

```bash
python -m src.migrate discover --prefix "EDUCACIONAL |"
python -m src.migrate run --dry-run
python -m src.migrate run
```

Comandos disponíveis: `discover`, `run`, `retry-failed`, `export`, `cleanup`.
```

- [ ] **Step 9: Criar `pytest.ini` na raiz**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
addopts = -v --tb=short
```

- [ ] **Step 10: Commit**

```bash
git init
git add .gitignore .env.example requirements.txt requirements-dev.txt README.md pytest.ini src/__init__.py tests/__init__.py data/.gitkeep logs/.gitkeep
git commit -m "chore: setup inicial do projeto de migração"
```

---

## Task 1: Models compartilhados (dataclasses + pydantic)

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Escrever teste falhando**

```python
# tests/test_models.py
from datetime import datetime
from src.models import VideoState, VideoEntry, FolderEntry


def test_video_entry_defaults():
    v = VideoEntry(
        panda_id="abc",
        panda_folder="EDUCACIONAL | Test",
        title="Aula 01",
    )
    assert v.state == VideoState.PENDING
    assert v.retry_count == 0
    assert v.sp_media_code is None
    assert v.last_error is None


def test_video_state_transitions_listed():
    assert VideoState.PENDING.value == "pending"
    assert VideoState.DONE.value == "done"
    assert VideoState.FAILED.value == "failed"


def test_folder_entry_minimal():
    f = FolderEntry(panda_folder_id="uuid-1", sp_folder_code=None)
    assert f.panda_folder_id == "uuid-1"
    assert f.sp_folder_code is None
```

- [ ] **Step 2: Rodar teste e ver falhar**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models'`

- [ ] **Step 3: Implementar `src/models.py`**

```python
"""Tipos compartilhados pelo pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class VideoState(str, Enum):
    PENDING = "pending"
    DOWNLOAD_REQUESTED = "download_requested"
    DOWNLOAD_READY = "download_ready"
    DOWNLOADED = "downloaded"
    SP_MEDIA_CREATED = "sp_media_created"
    SP_UPLOAD_URLS_READY = "sp_upload_urls_ready"
    UPLOADING = "uploading"
    SP_PROCESSING = "sp_processing"
    SP_COMPLETED = "sp_completed"
    DONE = "done"
    FAILED = "failed"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class FolderEntry:
    panda_folder_id: str
    sp_folder_code: Optional[str] = None


@dataclass
class VideoEntry:
    panda_id: str
    panda_folder: str
    title: str
    description: str = ""
    thumbnail_url: Optional[str] = None
    duration_sec: int = 0
    size_bytes: int = 0
    tags: list[str] = field(default_factory=list)
    state: VideoState = VideoState.PENDING
    local_video_path: Optional[str] = None
    local_thumb_path: Optional[str] = None
    sp_media_code: Optional[str] = None
    sp_embed_url: Optional[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
```

- [ ] **Step 4: Rodar teste e ver passar**

Run: `pytest tests/test_models.py -v`
Expected: PASS — 3 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: models compartilhados (VideoState, VideoEntry, FolderEntry)"
```

---

## Task 2: Manifest — load/save atômico

**Files:**
- Create: `src/manifest.py`
- Create: `tests/test_manifest.py`

- [ ] **Step 1: Teste falhando — load de arquivo inexistente retorna manifest vazio**

```python
# tests/test_manifest.py
from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import VideoEntry, VideoState


def test_load_nonexistent_returns_empty(tmp_path: Path):
    m = Manifest.load(tmp_path / "manifest.json")
    assert m.folders == {}
    assert m.videos == {}


def test_save_then_load_roundtrip(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="T1"))
    m.save()
    m2 = Manifest.load(path)
    assert "v1" in m2.videos
    assert m2.videos["v1"].title == "T1"
    assert m2.videos["v1"].state == VideoState.PENDING


def test_save_is_atomic(tmp_path: Path, monkeypatch):
    """Se o write falhar na metade, o arquivo original permanece intacto."""
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="ok"))
    m.save()

    # Substitui video por novo (mas vamos sabotar o save)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F1", title="novo"))

    # Sabota o os.replace para simular falha pós-write
    import src.manifest as mod
    def boom(*args, **kwargs):
        raise OSError("disco cheio")
    monkeypatch.setattr(mod.os, "replace", boom)

    with pytest.raises(OSError):
        m.save()

    # arquivo original ainda válido com título "ok"
    m_orig = Manifest.load(path)
    assert m_orig.videos["v1"].title == "ok"
```

- [ ] **Step 2: Rodar teste e ver falhar**

Run: `pytest tests/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `src/manifest.py`**

```python
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
```

- [ ] **Step 4: Rodar teste e ver passar**

Run: `pytest tests/test_manifest.py -v`
Expected: PASS — 3 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/manifest.py tests/test_manifest.py
git commit -m "feat: manifest persistente com write atômico"
```

---

## Task 3: Manifest — transitions e queries

**Files:**
- Modify: `tests/test_manifest.py`

- [ ] **Step 1: Adicionar testes para transition / videos_in_state / mark_failed**

Append em `tests/test_manifest.py`:

```python
def test_transition_updates_state_and_persists(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T"))
    m.transition("v1", VideoState.DOWNLOADED, local_video_path="data/downloads/v1.mp4")

    reloaded = Manifest.load(path)
    assert reloaded.videos["v1"].state == VideoState.DOWNLOADED
    assert reloaded.videos["v1"].local_video_path == "data/downloads/v1.mp4"


def test_videos_in_state_filters(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T1"))
    m.upsert_video(VideoEntry(panda_id="v2", panda_folder="F", title="T2", state=VideoState.DOWNLOADED))
    m.upsert_video(VideoEntry(panda_id="v3", panda_folder="F", title="T3", state=VideoState.DONE))

    pending = m.videos_in_state(VideoState.PENDING)
    assert {v.panda_id for v in pending} == {"v1"}

    in_flight = m.videos_in_state(VideoState.DOWNLOADED, VideoState.SP_PROCESSING)
    assert {v.panda_id for v in in_flight} == {"v2"}


def test_mark_failed_increments_retry(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T"))
    m.mark_failed("v1", "timeout on download")
    m.mark_failed("v1", "timeout again")

    v = m.videos["v1"]
    assert v.state == VideoState.FAILED
    assert v.retry_count == 2
    assert "timeout again" in v.last_error
```

- [ ] **Step 2: Rodar e ver passar (a implementação já existe)**

Run: `pytest tests/test_manifest.py -v`
Expected: PASS — 6 testes verdes.

- [ ] **Step 3: Commit**

```bash
git add tests/test_manifest.py
git commit -m "test: cobrir transitions, filtros e mark_failed do manifest"
```

---

## Task 4: Panda Client — list_folders e list_videos

**Files:**
- Create: `src/panda_client.py`
- Create: `tests/test_panda_client.py`

- [ ] **Step 1: Teste falhando — list_folders**

```python
# tests/test_panda_client.py
import pytest
from pytest_httpx import HTTPXMock

from src.panda_client import PandaClient


@pytest.mark.asyncio
async def test_list_folders_returns_parsed(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        json={"folders": [
            {"id": "f1", "name": "EDUCACIONAL | LIVES", "parent_folder_id": None},
            {"id": "f2", "name": "AREA COMERCIAL", "parent_folder_id": None},
        ]},
    )
    async with PandaClient(api_key="fake-key") as c:
        folders = await c.list_folders()

    assert len(folders) == 2
    assert folders[0].id == "f1"
    assert folders[0].name == "EDUCACIONAL | LIVES"


@pytest.mark.asyncio
async def test_list_folders_sends_auth_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        json={"folders": []},
    )
    async with PandaClient(api_key="my-secret") as c:
        await c.list_folders()

    req = httpx_mock.get_request()
    assert req.headers["Authorization"] == "my-secret"
    assert "Bearer" not in req.headers["Authorization"]


@pytest.mark.asyncio
async def test_list_videos_paginates(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/videos?folder_id=f1&page=1&limit=100",
        json={"videos": [{"id": f"v{i}", "title": f"T{i}", "folder_id": "f1",
                          "description": "", "length": 100, "size": 1000,
                          "tags": [], "thumbnail": "https://t/1.jpg",
                          "status": "converted", "created_at": "2025-01-01T00:00:00Z"}
                          for i in range(100)]},
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/videos?folder_id=f1&page=2&limit=100",
        json={"videos": [{"id": "v100", "title": "T100", "folder_id": "f1",
                          "description": "", "length": 50, "size": 500,
                          "tags": [], "thumbnail": "https://t/100.jpg",
                          "status": "converted", "created_at": "2025-01-01T00:00:00Z"}]},
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/videos?folder_id=f1&page=3&limit=100",
        json={"videos": []},
    )

    async with PandaClient(api_key="k") as c:
        videos = await c.list_videos("f1")

    assert len(videos) == 101
    assert videos[0].id == "v0"
    assert videos[-1].id == "v100"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_panda_client.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar versão inicial de `src/panda_client.py`**

```python
"""Cliente assíncrono da API Panda Video."""
from __future__ import annotations

from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field


PANDA_BASE_URL = "https://api-v2.pandavideo.com.br"


class PandaFolder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    parent_folder_id: Optional[str] = None


class PandaVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: str = ""
    folder_id: Optional[str] = None
    thumbnail: Optional[str] = None
    length: int = 0
    size: int = 0
    tags: list[str] = Field(default_factory=list)
    status: str = "unknown"
    created_at: Optional[str] = None


class PandaClient:
    def __init__(self, api_key: str, base_url: str = PANDA_BASE_URL, timeout: float = 60.0):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            http2=True,
        )

    async def __aenter__(self) -> "PandaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def list_folders(self, parent_folder_id: Optional[str] = None) -> list[PandaFolder]:
        params: dict[str, str] = {}
        if parent_folder_id:
            params["parent_folder_id"] = parent_folder_id
        r = await self._client.get(f"{self._base_url}/folders", params=params)
        r.raise_for_status()
        data = r.json()
        raw = data.get("folders", data) if isinstance(data, dict) else data
        return [PandaFolder.model_validate(item) for item in raw]

    async def list_videos(self, folder_id: str, limit: int = 100) -> list[PandaVideo]:
        videos: list[PandaVideo] = []
        page = 1
        while True:
            r = await self._client.get(
                f"{self._base_url}/videos",
                params={"folder_id": folder_id, "page": page, "limit": limit},
            )
            r.raise_for_status()
            data = r.json()
            batch = data.get("videos", data) if isinstance(data, dict) else data
            if not batch:
                break
            videos.extend(PandaVideo.model_validate(item) for item in batch)
            if len(batch) < limit:
                break
            page += 1
        return videos
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_panda_client.py -v`
Expected: PASS — 3 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/panda_client.py tests/test_panda_client.py
git commit -m "feat: PandaClient.list_folders e list_videos com paginação"
```

---

## Task 5: Panda Client — download assíncrono

**Files:**
- Modify: `src/panda_client.py`
- Modify: `tests/test_panda_client.py`

- [ ] **Step 1: Adicionar testes**

Append em `tests/test_panda_client.py`:

```python
@pytest.mark.asyncio
async def test_request_download_posts_payload(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://api-v2.pandavideo.com.br/download-async/v1",
        json={"status": "queued"},
    )
    async with PandaClient(api_key="k") as c:
        await c.request_download("v1", quality="original", title="Aula 01")

    req = httpx_mock.get_request()
    import json as _json
    body = _json.loads(req.content)
    assert body == {
        "quality": "original",
        "format": "video",
        "language": "pt-BR",
        "video_title": "Aula 01",
    }


@pytest.mark.asyncio
async def test_poll_download_returns_url_when_ready(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="GET",
        url="https://api-v2.pandavideo.com.br/download-async/v1/video/original/pt-BR",
        status_code=200,
        json={"url": "https://s3-signed.example/v1.mp4"},
    )
    async with PandaClient(api_key="k") as c:
        url = await c.poll_download("v1", quality="original")
    assert url == "https://s3-signed.example/v1.mp4"


@pytest.mark.asyncio
async def test_poll_download_returns_none_when_processing(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="GET",
        url="https://api-v2.pandavideo.com.br/download-async/v1/video/original/pt-BR",
        status_code=400,
        json={"message": "still processing"},
    )
    async with PandaClient(api_key="k") as c:
        url = await c.poll_download("v1", quality="original")
    assert url is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_panda_client.py -v -k "request_download or poll_download"`
Expected: FAIL — métodos não existem.

- [ ] **Step 3: Implementar os métodos em `src/panda_client.py`**

Append no final da classe `PandaClient`:

```python
    async def request_download(
        self,
        video_id: str,
        quality: str = "original",
        title: str = "",
        language: str = "pt-BR",
    ) -> None:
        payload = {
            "quality": quality,
            "format": "video",
            "language": language,
            "video_title": title,
        }
        r = await self._client.post(
            f"{self._base_url}/download-async/{video_id}",
            json=payload,
        )
        if r.status_code not in (200, 201, 202):
            r.raise_for_status()

    async def poll_download(
        self,
        video_id: str,
        quality: str = "original",
        language: str = "pt-BR",
    ) -> Optional[str]:
        """Retorna URL S3 assinada se pronto, None se ainda processando, raise em erro real."""
        r = await self._client.get(
            f"{self._base_url}/download-async/{video_id}/video/{quality}/{language}"
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("url") or data.get("download_url")
        if r.status_code == 400:
            return None
        r.raise_for_status()
        return None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_panda_client.py -v`
Expected: PASS — 6 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/panda_client.py tests/test_panda_client.py
git commit -m "feat: PandaClient.request_download e poll_download"
```

---

## Task 6: Panda Client — download streaming de arquivo

**Files:**
- Modify: `src/panda_client.py`
- Modify: `tests/test_panda_client.py`

- [ ] **Step 1: Adicionar teste**

Append em `tests/test_panda_client.py`:

```python
@pytest.mark.asyncio
async def test_download_file_writes_bytes(httpx_mock: HTTPXMock, tmp_path):
    httpx_mock.add_response(
        url="https://s3-signed.example/v1.mp4",
        content=b"fake-mp4-bytes",
        headers={"content-length": "14"},
    )
    dest = tmp_path / "v1.mp4"
    async with PandaClient(api_key="k") as c:
        size = await c.download_file("https://s3-signed.example/v1.mp4", dest)

    assert dest.read_bytes() == b"fake-mp4-bytes"
    assert size == 14
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_panda_client.py -v -k download_file`
Expected: FAIL — método não existe.

- [ ] **Step 3: Implementar `download_file`**

Append no final da classe `PandaClient`:

```python
    async def download_file(self, url: str, dest_path) -> int:
        """Baixa em streaming para dest_path. Retorna bytes escritos."""
        from pathlib import Path
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        # URL S3 assinada — usar client sem header de auth
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30.0)) as raw:
            async with raw.stream("GET", url) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        written += len(chunk)
        return written
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_panda_client.py -v`
Expected: PASS — 7 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/panda_client.py tests/test_panda_client.py
git commit -m "feat: PandaClient.download_file em streaming"
```

---

## Task 7: SmartPlayer Client — autenticação OAuth2 com cache

**Files:**
- Create: `src/smartplayer_client.py`
- Create: `tests/test_smartplayer_client.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_smartplayer_client.py
import json
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from src.smartplayer_client import SmartPlayerClient


@pytest.mark.asyncio
async def test_get_token_fetches_and_caches(httpx_mock: HTTPXMock, tmp_path: Path):
    httpx_mock.add_response(
        method="POST",
        url="https://services.scaleup.com.br/authentication/v1/oauth/token",
        json={"access_token": "tok-123", "expires_in": 604800, "token_type": "Bearer"},
    )
    cache = tmp_path / "token.json"
    async with SmartPlayerClient(
        client_id="cid",
        client_secret="csec",
        user_code="uc",
        token_cache_path=cache,
    ) as c:
        tok = await c.get_token()

    assert tok == "tok-123"
    assert cache.exists()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["access_token"] == "tok-123"
    assert "expires_at" in payload


@pytest.mark.asyncio
async def test_get_token_uses_cache_if_valid(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = tmp_path / "token.json"
    # Token válido por 1h
    from datetime import datetime, timedelta, timezone
    cache.write_text(json.dumps({
        "access_token": "cached-tok",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }), encoding="utf-8")

    async with SmartPlayerClient(
        client_id="cid", client_secret="csec", user_code="uc",
        token_cache_path=cache,
    ) as c:
        tok = await c.get_token()

    assert tok == "cached-tok"
    # nenhuma request foi feita
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_get_token_refreshes_when_near_expiry(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = tmp_path / "token.json"
    from datetime import datetime, timedelta, timezone
    # expira em 2 minutos — dentro da janela de 5min de refresh
    cache.write_text(json.dumps({
        "access_token": "old-tok",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
    }), encoding="utf-8")

    httpx_mock.add_response(
        method="POST",
        url="https://services.scaleup.com.br/authentication/v1/oauth/token",
        json={"access_token": "new-tok", "expires_in": 604800, "token_type": "Bearer"},
    )

    async with SmartPlayerClient(
        client_id="cid", client_secret="csec", user_code="uc",
        token_cache_path=cache,
    ) as c:
        tok = await c.get_token()

    assert tok == "new-tok"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_smartplayer_client.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `src/smartplayer_client.py`**

```python
"""Cliente assíncrono da API SmartPlayer (Scaleup)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict


SP_AUTH_URL = "https://services.scaleup.com.br/authentication/v1/oauth/token"
SP_BASE_URL = "https://services.scaleup.com.br/backoffice/v1"
SP_EMBED_URL_TEMPLATE = "https://player.scaleup.com.br/embed/{code}"
REFRESH_WINDOW_MIN = 5


class SPMedia(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str
    status: str
    urlsUpload: Optional[dict] = None


class SmartPlayerClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_code: str,
        token_cache_path: Path,
        base_url: str = SP_BASE_URL,
        timeout: float = 60.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_code = user_code
        self._token_cache_path = Path(token_cache_path)
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            http2=True,
        )

    async def __aenter__(self) -> "SmartPlayerClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def get_token(self) -> str:
        cached = self._read_token_cache()
        if cached and not self._near_expiry(cached["expires_at"]):
            return cached["access_token"]

        r = await self._client.post(
            SP_AUTH_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        r.raise_for_status()
        data = r.json()
        access = data["access_token"]
        ttl = int(data.get("expires_in", 604800))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        self._write_token_cache(access, expires_at)
        return access

    def _read_token_cache(self) -> Optional[dict]:
        if not self._token_cache_path.exists():
            return None
        try:
            return json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None

    def _write_token_cache(self, access_token: str, expires_at: str) -> None:
        self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_cache_path.write_text(
            json.dumps({"access_token": access_token, "expires_at": expires_at}),
            encoding="utf-8",
        )

    @staticmethod
    def _near_expiry(expires_at_iso: str) -> bool:
        expires = datetime.fromisoformat(expires_at_iso)
        return expires - datetime.now(timezone.utc) < timedelta(minutes=REFRESH_WINDOW_MIN)

    async def _authed_headers(self) -> dict[str, str]:
        tok = await self.get_token()
        return {
            "Authorization": f"Bearer {tok}",
            "X-User-Code": self._user_code,
        }
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_smartplayer_client.py -v`
Expected: PASS — 3 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/smartplayer_client.py tests/test_smartplayer_client.py
git commit -m "feat: SmartPlayerClient.get_token com cache e refresh"
```

---

## Task 8: SmartPlayer Client — folders

**Files:**
- Modify: `src/smartplayer_client.py`
- Modify: `tests/test_smartplayer_client.py`

- [ ] **Step 1: Adicionar testes**

Append em `tests/test_smartplayer_client.py`:

```python
def _stub_token_cache(tmp_path: Path) -> Path:
    from datetime import datetime, timedelta, timezone
    cache = tmp_path / "token.json"
    cache.write_text(json.dumps({
        "access_token": "tok",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }), encoding="utf-8")
    return cache


@pytest.mark.asyncio
async def test_create_folder_root(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = _stub_token_cache(tmp_path)
    httpx_mock.add_response(
        method="POST",
        url="https://services.scaleup.com.br/backoffice/v1/folders",
        json={"code": "sp-fold-1", "name": "EDUCACIONAL | LIVES"},
    )
    async with SmartPlayerClient("cid", "csec", "uc", cache) as c:
        code = await c.create_folder("EDUCACIONAL | LIVES")

    assert code == "sp-fold-1"
    req = httpx_mock.get_request()
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["X-User-Code"] == "uc"
    body = json.loads(req.content)
    assert body == {"name": "EDUCACIONAL | LIVES"}


@pytest.mark.asyncio
async def test_create_folder_with_parent(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = _stub_token_cache(tmp_path)
    httpx_mock.add_response(
        method="POST",
        url="https://services.scaleup.com.br/backoffice/v1/folders?root-folder-code=parent-x",
        json={"code": "sp-fold-2", "name": "Subpasta"},
    )
    async with SmartPlayerClient("cid", "csec", "uc", cache) as c:
        code = await c.create_folder("Subpasta", parent_code="parent-x")
    assert code == "sp-fold-2"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_smartplayer_client.py -v -k create_folder`
Expected: FAIL — método não existe.

- [ ] **Step 3: Implementar `create_folder` em `src/smartplayer_client.py`**

Append na classe `SmartPlayerClient`:

```python
    async def create_folder(self, name: str, parent_code: Optional[str] = None) -> str:
        headers = await self._authed_headers()
        headers["Content-Type"] = "application/json"
        params: dict[str, str] = {}
        if parent_code:
            params["root-folder-code"] = parent_code
        r = await self._client.post(
            f"{self._base_url}/folders",
            headers=headers,
            params=params,
            json={"name": name},
        )
        r.raise_for_status()
        data = r.json()
        return data["code"]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_smartplayer_client.py -v`
Expected: PASS — 5 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/smartplayer_client.py tests/test_smartplayer_client.py
git commit -m "feat: SmartPlayerClient.create_folder"
```

---

## Task 9: SmartPlayer Client — create_media, get_upload_urls, upload_binary, poll_status

**Files:**
- Modify: `src/smartplayer_client.py`
- Modify: `tests/test_smartplayer_client.py`

- [ ] **Step 1: Adicionar testes**

Append em `tests/test_smartplayer_client.py`:

```python
@pytest.mark.asyncio
async def test_create_media_returns_code(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = _stub_token_cache(tmp_path)
    httpx_mock.add_response(
        method="POST",
        url="https://services.scaleup.com.br/backoffice/v1/medias",
        json=[{"code": "media-xyz", "status": "DRAFT"}],
    )
    async with SmartPlayerClient("cid", "csec", "uc", cache) as c:
        code = await c.create_media(
            name="Aula 01",
            description="Intro",
            external_id="panda-v1",
            total_size=12345,
        )

    assert code == "media-xyz"
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == [{
        "name": "Aula 01",
        "description": "Intro",
        "externalId": "panda-v1",
        "totalSize": 12345,
        "publicMedia": True,
    }]


@pytest.mark.asyncio
async def test_get_upload_urls(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = _stub_token_cache(tmp_path)
    httpx_mock.add_response(
        method="GET",
        url="https://services.scaleup.com.br/backoffice/v1/medias/media-xyz",
        json={
            "code": "media-xyz",
            "status": "DRAFT",
            "urlsUpload": {
                "urlUploadVideo": "https://sp-upload.example/v",
                "urlUploadPoster": "https://sp-upload.example/p",
            },
        },
    )
    async with SmartPlayerClient("cid", "csec", "uc", cache) as c:
        urls = await c.get_upload_urls("media-xyz")
    assert urls == {
        "urlUploadVideo": "https://sp-upload.example/v",
        "urlUploadPoster": "https://sp-upload.example/p",
    }


@pytest.mark.asyncio
async def test_upload_binary_puts_file(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = _stub_token_cache(tmp_path)
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"\x00\x01\x02fake")

    httpx_mock.add_response(
        method="PUT",
        url="https://sp-upload.example/v",
        status_code=200,
        json={"success": True},
    )
    async with SmartPlayerClient("cid", "csec", "uc", cache) as c:
        await c.upload_binary(
            "https://sp-upload.example/v",
            file_path,
            content_type="video/mp4",
        )

    req = httpx_mock.get_request(method="PUT")
    assert req.headers["Content-Type"] == "video/mp4"
    assert req.content == b"\x00\x01\x02fake"


@pytest.mark.asyncio
async def test_poll_status(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = _stub_token_cache(tmp_path)
    httpx_mock.add_response(
        method="GET",
        url="https://services.scaleup.com.br/backoffice/v1/medias/media-xyz",
        json={"code": "media-xyz", "status": "COMPRESS_ENCODE"},
    )
    async with SmartPlayerClient("cid", "csec", "uc", cache) as c:
        status = await c.poll_status("media-xyz")
    assert status == "COMPRESS_ENCODE"


def test_embed_url():
    from src.smartplayer_client import build_embed_url
    assert build_embed_url("abc123") == "https://player.scaleup.com.br/embed/abc123"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_smartplayer_client.py -v -k "create_media or upload_urls or upload_binary or poll_status or embed_url"`
Expected: FAIL.

- [ ] **Step 3: Implementar métodos restantes**

Append em `src/smartplayer_client.py`, **fora da classe**:

```python
def build_embed_url(media_code: str) -> str:
    return SP_EMBED_URL_TEMPLATE.format(code=media_code)
```

Append **dentro** da classe `SmartPlayerClient`:

```python
    async def create_media(
        self,
        name: str,
        description: str,
        external_id: str,
        total_size: int,
        public_media: bool = True,
    ) -> str:
        headers = await self._authed_headers()
        headers["Content-Type"] = "application/json"
        payload = [{
            "name": name,
            "description": description,
            "externalId": external_id,
            "totalSize": total_size,
            "publicMedia": public_media,
        }]
        r = await self._client.post(
            f"{self._base_url}/medias",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data[0]["code"]
        return data["code"]

    async def get_upload_urls(self, media_code: str) -> dict[str, str]:
        headers = await self._authed_headers()
        r = await self._client.get(
            f"{self._base_url}/medias/{media_code}",
            headers=headers,
        )
        r.raise_for_status()
        urls = r.json().get("urlsUpload") or {}
        return urls

    async def upload_binary(self, url: str, file_path, content_type: str) -> None:
        from pathlib import Path
        path = Path(file_path)
        size = path.stat().st_size
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30.0)) as raw:
            with path.open("rb") as f:
                r = await raw.put(
                    url,
                    content=f.read(),
                    headers={
                        "Content-Type": content_type,
                        "Content-Length": str(size),
                    },
                )
        r.raise_for_status()

    async def poll_status(self, media_code: str) -> str:
        headers = await self._authed_headers()
        r = await self._client.get(
            f"{self._base_url}/medias/{media_code}",
            headers=headers,
        )
        r.raise_for_status()
        return r.json().get("status", "UNKNOWN")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_smartplayer_client.py -v`
Expected: PASS — 9 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/smartplayer_client.py tests/test_smartplayer_client.py
git commit -m "feat: SmartPlayerClient.create_media, upload, poll_status"
```

---

## Task 10: Discovery — popular manifest

**Files:**
- Create: `src/discovery.py`
- Create: `tests/test_discovery.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_discovery.py
from pathlib import Path

import pytest

from src.discovery import discover
from src.manifest import Manifest
from src.models import VideoState
from src.panda_client import PandaFolder, PandaVideo


class FakePandaClient:
    def __init__(self):
        self.folders = [
            PandaFolder(id="f1", name="EDUCACIONAL | LIVES", parent_folder_id=None),
            PandaFolder(id="f2", name="EDUCACIONAL | Curso", parent_folder_id=None),
            PandaFolder(id="f3", name="AREA COMERCIAL", parent_folder_id=None),
        ]
        self.videos = {
            "f1": [PandaVideo(id="v1", title="Aula 01", folder_id="f1", length=120,
                              size=10_000_000, thumbnail="https://t/v1.jpg",
                              status="converted")],
            "f2": [PandaVideo(id="v2", title="Aula 02", folder_id="f2", length=300,
                              size=20_000_000, thumbnail="https://t/v2.jpg",
                              status="converted"),
                   PandaVideo(id="v3", title="Aula 03", folder_id="f2", length=400,
                              size=30_000_000, thumbnail="https://t/v3.jpg",
                              status="converting")],
            "f3": [PandaVideo(id="v4", title="Comercial", folder_id="f3", length=60,
                              size=5_000_000, thumbnail="https://t/v4.jpg",
                              status="converted")],
        }

    async def list_folders(self):
        return self.folders

    async def list_videos(self, folder_id, limit=100):
        return self.videos.get(folder_id, [])


@pytest.mark.asyncio
async def test_discovery_filters_by_prefix(tmp_path: Path):
    panda = FakePandaClient()
    m = Manifest.load(tmp_path / "m.json")
    await discover(panda, m, prefix="EDUCACIONAL |")

    # 3 vídeos das pastas EDUCACIONAL, mas v3 (converting) é pulado
    assert {"v1", "v2"} == set(m.videos.keys())
    assert "EDUCACIONAL | LIVES" in m.folders
    assert "EDUCACIONAL | Curso" in m.folders
    assert "AREA COMERCIAL" not in m.folders


@pytest.mark.asyncio
async def test_discovery_persists_metadata(tmp_path: Path):
    panda = FakePandaClient()
    m = Manifest.load(tmp_path / "m.json")
    await discover(panda, m, prefix="EDUCACIONAL |")

    v1 = m.videos["v1"]
    assert v1.title == "Aula 01"
    assert v1.duration_sec == 120
    assert v1.size_bytes == 10_000_000
    assert v1.thumbnail_url == "https://t/v1.jpg"
    assert v1.state == VideoState.PENDING
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `src/discovery.py`**

```python
"""Discovery: lista pastas/vídeos do Panda e popula o manifest."""
from __future__ import annotations

from datetime import datetime, timezone

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState


async def discover(panda, manifest: Manifest, prefix: str) -> None:
    """Popula `manifest` com pastas que comecem com `prefix` e seus vídeos `converted`."""
    folders = await panda.list_folders()
    selected = [f for f in folders if f.name.startswith(prefix)]
    manifest.discovered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for folder in selected:
        manifest.upsert_folder(
            folder.name,
            FolderEntry(panda_folder_id=folder.id, sp_folder_code=None),
        )
        videos = await panda.list_videos(folder.id)
        for v in videos:
            if v.status != "converted":
                continue
            if v.id in manifest.videos:
                continue
            manifest.upsert_video(VideoEntry(
                panda_id=v.id,
                panda_folder=folder.name,
                title=v.title,
                description=v.description,
                thumbnail_url=v.thumbnail,
                duration_sec=v.length,
                size_bytes=v.size,
                tags=list(v.tags),
                state=VideoState.PENDING,
            ))

    manifest.save()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_discovery.py -v`
Expected: PASS — 2 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/discovery.py tests/test_discovery.py
git commit -m "feat: discovery filtra pastas por prefixo e popula manifest"
```

---

## Task 11: Pipeline — worker de download

**Files:**
- Create: `src/pipeline.py`
- Create: `tests/test_pipeline_download.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_pipeline_download.py
import asyncio
from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import VideoEntry, VideoState
from src.pipeline import download_one


class FakePanda:
    def __init__(self):
        self.requested = []
        self.poll_calls = 0

    async def request_download(self, video_id, quality, title):
        self.requested.append((video_id, quality, title))

    async def poll_download(self, video_id, quality="original", language="pt-BR"):
        self.poll_calls += 1
        if self.poll_calls < 2:
            return None
        return f"https://signed/{video_id}.mp4"

    async def download_file(self, url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"video-bytes")
        return 11


@pytest.mark.asyncio
async def test_download_one_full_cycle(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T1"))

    panda = FakePanda()
    download_dir = tmp_path / "downloads"
    await download_one(panda, m, "v1", download_dir, quality="original", poll_interval=0)

    v = m.videos["v1"]
    assert v.state == VideoState.DOWNLOADED
    assert v.local_video_path is not None
    assert Path(v.local_video_path).exists()
    assert panda.requested == [("v1", "original", "T1")]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_pipeline_download.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `src/pipeline.py` (parcial)**

```python
"""Pipeline assíncrono: workers de download e upload."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.manifest import Manifest
from src.models import VideoState

logger = logging.getLogger(__name__)


async def download_one(
    panda,
    manifest: Manifest,
    video_id: str,
    download_dir: Path,
    quality: str = "original",
    poll_interval: float = 30.0,
    poll_timeout: float = 60 * 60,
) -> None:
    """Executa o sub-pipeline de download para um único vídeo, atualizando o manifest."""
    v = manifest.videos[video_id]

    if v.state == VideoState.PENDING:
        await panda.request_download(video_id, quality, v.title)
        manifest.transition(video_id, VideoState.DOWNLOAD_REQUESTED)

    if v.state in (VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY):
        elapsed = 0.0
        url: str | None = None
        while elapsed < poll_timeout:
            url = await panda.poll_download(video_id, quality)
            if url:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        if not url:
            raise TimeoutError(f"poll_download timeout para {video_id}")
        manifest.transition(video_id, VideoState.DOWNLOAD_READY)
        download_dir.mkdir(parents=True, exist_ok=True)
        dest = download_dir / f"{video_id}.mp4"
        await panda.download_file(url, dest)
        manifest.transition(
            video_id, VideoState.DOWNLOADED,
            local_video_path=str(dest),
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_pipeline_download.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline.py tests/test_pipeline_download.py
git commit -m "feat: pipeline.download_one (panda → disco)"
```

---

## Task 12: Pipeline — worker de upload

**Files:**
- Modify: `src/pipeline.py`
- Create: `tests/test_pipeline_upload.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_pipeline_upload.py
from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState
from src.pipeline import upload_one


class FakeSP:
    def __init__(self):
        self.created = []
        self.upload_calls = 0
        self.poll_calls = 0

    async def create_folder(self, name, parent_code=None):
        return f"sp-folder-{name[:3]}"

    async def create_media(self, name, description, external_id, total_size, public_media=True):
        self.created.append(external_id)
        return f"sp-media-{external_id}"

    async def get_upload_urls(self, code):
        return {
            "urlUploadVideo": f"https://up/{code}/v",
            "urlUploadPoster": f"https://up/{code}/p",
        }

    async def upload_binary(self, url, file_path, content_type):
        self.upload_calls += 1

    async def poll_status(self, code):
        self.poll_calls += 1
        return "COMPLETED" if self.poll_calls >= 2 else "COMPRESS_ENCODE"


@pytest.mark.asyncio
async def test_upload_one_full_cycle(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    # pasta já mapeada
    m.upsert_folder("EDUCACIONAL | F", FolderEntry(panda_folder_id="f1", sp_folder_code="sp-folder-EDU"))
    # vídeo pronto para upload
    video_path = tmp_path / "downloads" / "v1.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"x" * 1024)
    m.upsert_video(VideoEntry(
        panda_id="v1", panda_folder="EDUCACIONAL | F", title="Aula 01",
        description="Intro", size_bytes=1024,
        state=VideoState.DOWNLOADED, local_video_path=str(video_path),
    ))

    sp = FakeSP()
    await upload_one(sp, m, "v1", poll_interval=0, cleanup=True)

    v = m.videos["v1"]
    assert v.state == VideoState.DONE
    assert v.sp_media_code == "sp-media-v1"
    assert v.sp_embed_url == "https://player.scaleup.com.br/embed/sp-media-v1"
    assert not Path(v.local_video_path).exists()  # cleanup
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_pipeline_upload.py -v`
Expected: FAIL — função não existe.

- [ ] **Step 3: Implementar `upload_one` em `src/pipeline.py`**

Append em `src/pipeline.py`:

```python
from src.smartplayer_client import build_embed_url


SP_TERMINAL_STATUSES = {"COMPLETED"}
SP_ERROR_STATUSES = {"ERROR"}


async def upload_one(
    sp,
    manifest: Manifest,
    video_id: str,
    poll_interval: float = 60.0,
    poll_timeout: float = 2 * 60 * 60,
    cleanup: bool = True,
) -> None:
    """Sub-pipeline: vídeo no disco → media SP → upload → encoding → DONE."""
    v = manifest.videos[video_id]

    if v.state == VideoState.DOWNLOADED:
        code = await sp.create_media(
            name=v.title,
            description=v.description,
            external_id=v.panda_id,
            total_size=v.size_bytes,
        )
        manifest.transition(video_id, VideoState.SP_MEDIA_CREATED, sp_media_code=code)

    if v.state == VideoState.SP_MEDIA_CREATED:
        urls = await sp.get_upload_urls(v.sp_media_code)
        manifest.transition(video_id, VideoState.SP_UPLOAD_URLS_READY)
        v._upload_urls = urls  # type: ignore[attr-defined]

    if v.state == VideoState.SP_UPLOAD_URLS_READY:
        urls = getattr(v, "_upload_urls", None) or await sp.get_upload_urls(v.sp_media_code)
        await sp.upload_binary(urls["urlUploadVideo"], v.local_video_path, "video/mp4")
        if v.local_thumb_path:
            await sp.upload_binary(urls["urlUploadPoster"], v.local_thumb_path, "image/jpeg")
        manifest.transition(video_id, VideoState.UPLOADING)
        manifest.transition(video_id, VideoState.SP_PROCESSING)

    if v.state == VideoState.SP_PROCESSING:
        elapsed = 0.0
        status = None
        while elapsed < poll_timeout:
            status = await sp.poll_status(v.sp_media_code)
            if status in SP_TERMINAL_STATUSES:
                break
            if status in SP_ERROR_STATUSES:
                raise RuntimeError(f"SP retornou ERROR para {video_id}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        if status not in SP_TERMINAL_STATUSES:
            raise TimeoutError(f"poll_status timeout para {video_id}")
        manifest.transition(
            video_id, VideoState.SP_COMPLETED,
            sp_embed_url=build_embed_url(v.sp_media_code),
        )

    if v.state == VideoState.SP_COMPLETED:
        if cleanup and v.local_video_path:
            try:
                Path(v.local_video_path).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("cleanup falhou para %s: %s", video_id, e)
            if v.local_thumb_path:
                Path(v.local_thumb_path).unlink(missing_ok=True)
        manifest.transition(video_id, VideoState.DONE)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_pipeline_upload.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline.py tests/test_pipeline_upload.py
git commit -m "feat: pipeline.upload_one (disco → SmartPlayer → cleanup)"
```

---

## Task 13: Pipeline — orquestrador concorrente

**Files:**
- Modify: `src/pipeline.py`
- Create: `tests/test_pipeline_orchestrator.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_pipeline_orchestrator.py
from pathlib import Path

import pytest

from src.manifest import Manifest
from src.models import FolderEntry, VideoEntry, VideoState
from src.pipeline import run_pipeline


class FakePanda:
    async def request_download(self, video_id, quality, title): pass
    async def poll_download(self, video_id, quality="original", language="pt-BR"):
        return f"https://signed/{video_id}.mp4"
    async def download_file(self, url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"x" * 100)
        return 100


class FakeSP:
    async def create_folder(self, name, parent_code=None):
        return f"sp-{name[:3]}"
    async def create_media(self, name, description, external_id, total_size, public_media=True):
        return f"m-{external_id}"
    async def get_upload_urls(self, code):
        return {"urlUploadVideo": "https://up/v", "urlUploadPoster": "https://up/p"}
    async def upload_binary(self, url, file_path, content_type): pass
    async def poll_status(self, code):
        return "COMPLETED"


@pytest.mark.asyncio
async def test_run_pipeline_completes_all_pending(tmp_path: Path):
    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder("F1", FolderEntry(panda_folder_id="f1"))
    for i in range(5):
        m.upsert_video(VideoEntry(
            panda_id=f"v{i}", panda_folder="F1", title=f"T{i}", size_bytes=100,
        ))
    m.save()

    await run_pipeline(
        panda=FakePanda(),
        sp=FakeSP(),
        manifest=m,
        download_dir=tmp_path / "downloads",
        max_download_concurrency=2,
        max_upload_concurrency=2,
        poll_interval=0,
    )

    for i in range(5):
        v = m.videos[f"v{i}"]
        assert v.state == VideoState.DONE
        assert v.sp_embed_url is not None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_pipeline_orchestrator.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar `run_pipeline` em `src/pipeline.py`**

Append em `src/pipeline.py`:

```python
from src.models import FolderEntry


async def _ensure_sp_folder(sp, manifest: Manifest, folder_name: str) -> str:
    f = manifest.folders.get(folder_name)
    if f and f.sp_folder_code:
        return f.sp_folder_code
    code = await sp.create_folder(folder_name)
    new_entry = FolderEntry(
        panda_folder_id=f.panda_folder_id if f else "",
        sp_folder_code=code,
    )
    manifest.upsert_folder(folder_name, new_entry)
    manifest.save()
    return code


async def run_pipeline(
    panda,
    sp,
    manifest: Manifest,
    download_dir: Path,
    max_download_concurrency: int = 3,
    max_upload_concurrency: int = 3,
    poll_interval: float = 30.0,
    quality: str = "original",
) -> None:
    """Orquestra workers de download e upload via filas asyncio."""
    # Garante pasta no SP para cada folder do manifest
    for folder_name in list(manifest.folders.keys()):
        await _ensure_sp_folder(sp, manifest, folder_name)

    # Filas
    to_download: asyncio.Queue[str] = asyncio.Queue()
    to_upload: asyncio.Queue[str] = asyncio.Queue()

    # Popula com vídeos pendentes (qualquer estado pré-DOWNLOADED)
    pre_download = (VideoState.PENDING, VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY)
    pre_upload = (VideoState.DOWNLOADED, VideoState.SP_MEDIA_CREATED,
                  VideoState.SP_UPLOAD_URLS_READY, VideoState.UPLOADING, VideoState.SP_PROCESSING,
                  VideoState.SP_COMPLETED)

    for v in manifest.videos_in_state(*pre_download):
        await to_download.put(v.panda_id)
    for v in manifest.videos_in_state(*pre_upload):
        await to_upload.put(v.panda_id)

    sentinel = "__STOP__"

    async def download_worker():
        while True:
            vid = await to_download.get()
            if vid == sentinel:
                to_download.task_done()
                return
            try:
                await download_one(panda, manifest, vid, download_dir, quality, poll_interval)
                await to_upload.put(vid)
            except Exception as e:
                manifest.mark_failed(vid, f"download: {e!r}")
                logger.exception("download falhou para %s", vid)
            finally:
                to_download.task_done()

    async def upload_worker():
        while True:
            vid = await to_upload.get()
            if vid == sentinel:
                to_upload.task_done()
                return
            try:
                await upload_one(sp, manifest, vid, poll_interval)
            except Exception as e:
                manifest.mark_failed(vid, f"upload: {e!r}")
                logger.exception("upload falhou para %s", vid)
            finally:
                to_upload.task_done()

    downloaders = [asyncio.create_task(download_worker()) for _ in range(max_download_concurrency)]
    uploaders = [asyncio.create_task(upload_worker()) for _ in range(max_upload_concurrency)]

    # Aguarda fila de download esvaziar e then sinaliza downloaders
    await to_download.join()
    for _ in downloaders:
        await to_download.put(sentinel)
    await asyncio.gather(*downloaders)

    # Aguarda fila de upload esvaziar (alimentada pelos downloaders) e sinaliza uploaders
    await to_upload.join()
    for _ in uploaders:
        await to_upload.put(sentinel)
    await asyncio.gather(*uploaders)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_pipeline_orchestrator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline.py tests/test_pipeline_orchestrator.py
git commit -m "feat: orquestrador run_pipeline com workers concorrentes"
```

---

## Task 14: Exporter — Markdown e CSV

**Files:**
- Create: `src/exporter.py`
- Create: `tests/test_exporter.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_exporter.py
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_exporter.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar `src/exporter.py`**

```python
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_exporter.py -v`
Expected: PASS — 2 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/exporter.py tests/test_exporter.py
git commit -m "feat: exporter MD agrupado por pasta + CSV plano"
```

---

## Task 15: Config loader (.env → settings)

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_config.py
import pytest

from src.config import Settings


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("PANDA_API_KEY", "panda-key")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "cid")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "csec")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "uc")
    monkeypatch.setenv("MAX_DOWNLOAD_CONCURRENCY", "5")

    s = Settings.from_env()
    assert s.panda_api_key == "panda-key"
    assert s.sp_client_id == "cid"
    assert s.max_download_concurrency == 5
    assert s.max_upload_concurrency == 3  # default
    assert s.panda_quality == "original"  # default


def test_settings_missing_required(monkeypatch):
    monkeypatch.delenv("PANDA_API_KEY", raising=False)
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "x")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "x")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "x")
    with pytest.raises(SystemExit) as exc:
        Settings.from_env()
    assert "PANDA_API_KEY" in str(exc.value)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_config.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar `src/config.py`**

```python
"""Carregamento e validação de configuração via .env."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} ausente em .env — preencha e tente novamente")
    return v


@dataclass
class Settings:
    panda_api_key: str
    sp_client_id: str
    sp_client_secret: str
    sp_user_code: str
    max_download_concurrency: int = 3
    max_upload_concurrency: int = 3
    max_disk_usage_gb: int = 10
    panda_quality: str = "original"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None) -> "Settings":
        load_dotenv(dotenv_path)
        return cls(
            panda_api_key=_require("PANDA_API_KEY"),
            sp_client_id=_require("SMARTPLAYER_CLIENT_ID"),
            sp_client_secret=_require("SMARTPLAYER_CLIENT_SECRET"),
            sp_user_code=_require("SMARTPLAYER_USER_CODE"),
            max_download_concurrency=int(os.environ.get("MAX_DOWNLOAD_CONCURRENCY", "3")),
            max_upload_concurrency=int(os.environ.get("MAX_UPLOAD_CONCURRENCY", "3")),
            max_disk_usage_gb=int(os.environ.get("MAX_DISK_USAGE_GB", "10")),
            panda_quality=os.environ.get("PANDA_QUALITY", "original"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_config.py -v`
Expected: PASS — 2 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: Settings carregadas e validadas a partir de .env"
```

---

## Task 16: CLI — comando `discover`

**Files:**
- Create: `src/migrate.py`
- Create: `tests/test_migrate_cli.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_migrate_cli.py
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.migrate import build_parser, cmd_discover
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
        # 2º arg é manifest, 3º é prefix
        _, manifest_arg, kwargs = disc.await_args.args[0:2] + (disc.await_args.kwargs,)


def test_parser_has_subcommands():
    p = build_parser()
    args = p.parse_args(["run", "--dry-run"])
    assert args.command == "run"
    assert args.dry_run is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_migrate_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar `src/migrate.py` (esqueleto + discover)**

```python
"""CLI principal do migrador Panda → SmartPlayer."""
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
from src.panda_client import PandaClient
from src.smartplayer_client import SmartPlayerClient


DEFAULT_MANIFEST = Path("data/manifest.json")
DEFAULT_TOKEN_CACHE = Path("data/token_cache.json")
DEFAULT_DOWNLOAD_DIR = Path("data/downloads")
DEFAULT_LOG_MD = Path("data/migration_log.md")
DEFAULT_LOG_CSV = Path("data/migration_log.csv")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="migrate", description="Panda Video → SmartPlayer")
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("discover", help="Lista pastas/vídeos do Panda e popula manifest")
    pd.add_argument("--prefix", default="EDUCACIONAL |")

    pr = sub.add_parser("run", help="Executa pipeline de migração")
    pr.add_argument("--dry-run", action="store_true")
    pr.add_argument("--resume", action="store_true")

    sub.add_parser("retry-failed", help="Reprocessa vídeos no estado FAILED")
    sub.add_parser("export", help="Gera migration_log.md e .csv a partir do manifest")
    sub.add_parser("cleanup", help="Apaga MP4s locais de vídeos com state=DONE")
    return p


async def cmd_discover(args, manifest_path: Path = DEFAULT_MANIFEST) -> None:
    settings = Settings.from_env()
    manifest = Manifest.load(manifest_path)
    async with PandaClient(api_key=settings.panda_api_key) as panda:
        await discover(panda, manifest, prefix=args.prefix)
    print(f"Discovery completa. {len(manifest.videos)} vídeos em {len(manifest.folders)} pastas.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings_log = Settings.from_env() if args.command != "export" else None
    if settings_log:
        logging.basicConfig(
            level=getattr(logging, settings_log.log_level),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    if args.command == "discover":
        asyncio.run(cmd_discover(args))
        return 0
    print(f"Comando {args.command!r} ainda não implementado.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_migrate_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/migrate.py tests/test_migrate_cli.py
git commit -m "feat: CLI esqueleto + comando discover"
```

---

## Task 17: CLI — comandos `run`, `dry-run`, `resume`

**Files:**
- Modify: `src/migrate.py`
- Modify: `tests/test_migrate_cli.py`

- [ ] **Step 1: Adicionar testes**

Append em `tests/test_migrate_cli.py`:

```python
@pytest.mark.asyncio
async def test_cmd_run_dry_run_prints_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PANDA_API_KEY", "k")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "x")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "x")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "x")

    from src.manifest import Manifest
    from src.models import VideoEntry
    from src.migrate import cmd_run
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
    assert "2" in out  # 2 vídeos
    assert "286" in out or "0.28" in out or "MB" in out  # tamanho total estimado
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_migrate_cli.py -v -k dry_run`
Expected: FAIL.

- [ ] **Step 3: Implementar `cmd_run` em `src/migrate.py`**

Append:

```python
from src.models import VideoState
from src.pipeline import run_pipeline


def _format_plan(manifest: Manifest) -> str:
    pending = manifest.videos_in_state(
        VideoState.PENDING, VideoState.DOWNLOAD_REQUESTED, VideoState.DOWNLOAD_READY,
        VideoState.DOWNLOADED, VideoState.SP_MEDIA_CREATED, VideoState.SP_UPLOAD_URLS_READY,
        VideoState.UPLOADING, VideoState.SP_PROCESSING, VideoState.SP_COMPLETED,
    )
    total_bytes = sum(v.size_bytes for v in pending)
    largest = max((v.size_bytes for v in pending), default=0)
    lines = [
        "📦 Plano de migração",
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
        await run_pipeline(
            panda=panda,
            sp=sp,
            manifest=manifest,
            download_dir=download_dir,
            max_download_concurrency=settings.max_download_concurrency,
            max_upload_concurrency=settings.max_upload_concurrency,
            quality=settings.panda_quality,
        )

    export_markdown(manifest, DEFAULT_LOG_MD)
    export_csv(manifest, DEFAULT_LOG_CSV)
    print(f"\nLogs gerados em {DEFAULT_LOG_MD} e {DEFAULT_LOG_CSV}")
```

E atualize `main` para rotear `run`:

```python
    if args.command == "run":
        asyncio.run(cmd_run(args))
        return 0
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_migrate_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/migrate.py tests/test_migrate_cli.py
git commit -m "feat: CLI run/dry-run com plano e execução do pipeline"
```

---

## Task 18: CLI — `retry-failed`, `export`, `cleanup`

**Files:**
- Modify: `src/migrate.py`
- Modify: `tests/test_migrate_cli.py`

- [ ] **Step 1: Adicionar testes**

Append em `tests/test_migrate_cli.py`:

```python
@pytest.mark.asyncio
async def test_cmd_retry_failed_resets_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PANDA_API_KEY", "k")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "x")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "x")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "x")

    from src.manifest import Manifest
    from src.models import VideoEntry, VideoState
    from src.migrate import cmd_retry_failed

    m = Manifest.load(tmp_path / "manifest.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T", state=VideoState.FAILED, last_error="x"))
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
    from src.migrate import cmd_export

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
    from src.migrate import cmd_cleanup

    f = tmp_path / "v1.mp4"
    f.write_bytes(b"x")
    m = Manifest.load(tmp_path / "manifest.json")
    m.upsert_video(VideoEntry(panda_id="v1", panda_folder="F", title="T",
                              state=VideoState.DONE, local_video_path=str(f)))
    m.save()

    cmd_cleanup(manifest_path=tmp_path / "manifest.json")
    assert not f.exists()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_migrate_cli.py -v -k "retry or export or cleanup"`
Expected: FAIL.

- [ ] **Step 3: Implementar comandos restantes em `src/migrate.py`**

Append:

```python
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
            if v.local_thumb_path:
                pt = Path(v.local_thumb_path)
                if pt.exists():
                    pt.unlink()
    print(f"Arquivos removidos: {removed}")
```

Atualize `main` para rotear os novos comandos:

```python
    if args.command == "retry-failed":
        asyncio.run(cmd_retry_failed(args))
        return 0
    if args.command == "export":
        cmd_export()
        return 0
    if args.command == "cleanup":
        cmd_cleanup()
        return 0
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_migrate_cli.py -v`
Expected: PASS — todos os testes do CLI verdes.

- [ ] **Step 5: Commit**

```bash
git add src/migrate.py tests/test_migrate_cli.py
git commit -m "feat: CLI retry-failed, export, cleanup"
```

---

## Task 19: Logging estruturado JSON-lines

**Files:**
- Create: `src/logging_setup.py`
- Modify: `src/migrate.py`
- Create: `tests/test_logging.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_logging.py
import json
import logging
from pathlib import Path

from src.logging_setup import configure_logging


def test_configure_logging_writes_jsonl(tmp_path: Path):
    log_file = tmp_path / "migration.log"
    configure_logging(log_file, level="INFO")
    logger = logging.getLogger("test")
    logger.info("hello", extra={"video_id": "v1", "state": "downloaded"})

    for h in logging.getLogger().handlers:
        h.flush()

    content = log_file.read_text(encoding="utf-8").strip()
    line = json.loads(content.splitlines()[-1])
    assert line["msg"] == "hello"
    assert line["level"] == "INFO"
    assert line["video_id"] == "v1"
    assert "ts" in line
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_logging.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar `src/logging_setup.py`**

```python
"""Logging estruturado em JSON-lines."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


_STANDARD = {"name", "msg", "args", "levelname", "levelno", "pathname", "filename",
             "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
             "created", "msecs", "relativeCreated", "thread", "threadName",
             "processName", "process", "message", "asctime"}


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _STANDARD or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_path: Path, level: str = "INFO") -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(JsonLinesFormatter())
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)
```

Atualizar `src/migrate.py` (substituir o `logging.basicConfig` em `main`):

```python
from datetime import datetime
from src.logging_setup import configure_logging

# ...dentro de main(), trocar o bloco basicConfig por:
    settings = Settings.from_env()
    log_path = Path("logs") / f"migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    configure_logging(log_path, level=settings.log_level)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_logging.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/logging_setup.py src/migrate.py tests/test_logging.py
git commit -m "feat: logging estruturado JSON-lines"
```

---

## Task 20: Retry com tenacity nos métodos de cliente

**Files:**
- Modify: `src/panda_client.py`
- Modify: `src/smartplayer_client.py`
- Create: `tests/test_retry.py`

- [ ] **Step 1: Teste falhando**

```python
# tests/test_retry.py
import pytest
from pytest_httpx import HTTPXMock

from src.panda_client import PandaClient


@pytest.mark.asyncio
async def test_list_folders_retries_on_5xx(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        json={"folders": [{"id": "f1", "name": "OK"}]},
    )
    async with PandaClient(api_key="k") as c:
        folders = await c.list_folders()
    assert folders[0].name == "OK"
    assert len(httpx_mock.get_requests()) == 3
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_retry.py -v`
Expected: FAIL (sem retry, primeira 500 explode).

- [ ] **Step 3: Adicionar decorator de retry em `src/panda_client.py`**

No topo de `src/panda_client.py`:

```python
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type,
)


def _retry_http():
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.01, min=0, max=0.1),  # rápido em testes
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        reraise=True,
    )
```

Aplicar em `list_folders`, `list_videos`, `request_download`, `poll_download`:

```python
    @_retry_http()
    async def list_folders(self, parent_folder_id: Optional[str] = None) -> list[PandaFolder]:
        # ... corpo atual
```

(repita o decorator nas demais)

Importante: o `_retry_http` precisa estar fora da classe (módulo).

Aplicar mesma estratégia em `src/smartplayer_client.py` para `create_folder`, `create_media`, `get_upload_urls`, `poll_status`.

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_retry.py -v && pytest -v`
Expected: PASS — todos os testes anteriores continuam verdes.

- [ ] **Step 5: Commit**

```bash
git add src/panda_client.py src/smartplayer_client.py tests/test_retry.py
git commit -m "feat: retry com backoff em clientes HTTP via tenacity"
```

---

## Task 21: Wire-up final e smoke test manual

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rodar suíte completa**

Run: `pytest -v`
Expected: PASS — todos os testes verdes (~30+ testes).

- [ ] **Step 2: Atualizar README com instruções completas**

Substituir conteúdo de `README.md`:

```markdown
# Migração Panda Video → SmartPlayer

Script de migração one-shot para vídeos do Panda Video → SmartPlayer (Scaleup), preservando estrutura de pastas e metadados.

Ver spec completo em [docs/superpowers/specs/2026-05-21-panda-to-smartplayer-migration-design.md](docs/superpowers/specs/2026-05-21-panda-to-smartplayer-migration-design.md).

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
# preencher PANDA_API_KEY, SMARTPLAYER_*
```

## Uso

```powershell
# 1. Descoberta (popula data/manifest.json)
python -m src.migrate discover --prefix "EDUCACIONAL |"

# 2. Conferir o plano antes de baixar nada
python -m src.migrate run --dry-run

# 3. Executar migração
python -m src.migrate run

# 4. Em caso de falhas, reprocessar os que falharam
python -m src.migrate retry-failed

# 5. Gerar logs tabelados a qualquer momento
python -m src.migrate export

# 6. Limpar MP4s locais de vídeos já COMPLETED
python -m src.migrate cleanup
```

## Output

- `data/manifest.json` — estado persistente (resumível)
- `data/migration_log.md` — tabela humana, agrupada por pasta
- `data/migration_log.csv` — planilha plana (Excel/Sheets)
- `logs/migration_YYYYMMDD_HHMMSS.log` — log estruturado JSON-lines

## Variáveis de ambiente

Ver `.env.example`. Defaults:
- `MAX_DOWNLOAD_CONCURRENCY=3`
- `MAX_UPLOAD_CONCURRENCY=3`
- `MAX_DISK_USAGE_GB=10`
- `PANDA_QUALITY=original`
- `LOG_LEVEL=INFO`

## Testes

```powershell
pytest -v
```
```

- [ ] **Step 3: Smoke test manual com `discover`**

Pré-condição: `.env` preenchido com `PANDA_API_KEY` válida.

Run: `python -m src.migrate discover --prefix "EDUCACIONAL |"`
Expected: imprime contagem de pastas/vídeos e cria `data/manifest.json` populado.

- [ ] **Step 4: Smoke test manual com `run --dry-run`**

Run: `python -m src.migrate run --dry-run`
Expected: imprime o plano (pastas, vídeos pendentes, GB total). Não baixa nada.

- [ ] **Step 5: Commit final**

```bash
git add README.md
git commit -m "docs: README com instruções de setup e uso"
```

---

## Notas de execução

- **Ordem dos comandos no fluxo real:** sempre `discover` primeiro, depois `run --dry-run` para revisar, depois `run`.
- **Resumibilidade:** se `run` for interrompido, basta executar de novo (sem `--resume` — o manifest é a fonte da verdade; vídeos em estados intermediários são retomados naturalmente).
- **Cuidado com `retry-failed`:** reseta o `last_error` e devolve para `PENDING`. Se a falha foi por payload inválido, retentar não ajuda.
- **Após sucesso completo:** todos os vídeos têm `state=DONE`, `data/downloads/` está vazio, e `data/migration_log.csv` está pronto para alimentar a plataforma.
