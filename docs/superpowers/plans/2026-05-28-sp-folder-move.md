# SP Folder Move — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatizar o move de cada vídeo para sua pasta correta no SmartPlayer após upload, removendo o workaround de prefixo no título.

**Architecture:** Adiciona estado `SP_MOVED` entre `SP_COMPLETED` e `DONE`; o move é feito inline em `upload_one` logo após o encoding confirmar; criação de pastas passa de upfront (em `run_pipeline`) para lazy (em `upload_one` via `_ensure_sp_folder` já existente).

**Tech Stack:** Python 3.14, httpx, pydantic, pytest, pytest-asyncio, pytest-httpx

---

## Bugs pré-existentes corrigidos neste plano

Três testes estavam falhando antes desta feature:
1. `test_create_media_returns_code` — assertava `code == "media-xyz"` mas `create_media` retorna `SPMedia`, não string.
2. `test_upload_one_full_cycle` — `FakeSP.create_media` retornava string em vez de `SPMedia`; `upload_one` acessa `.code`.
3. `test_run_pipeline_completes_all_pending` — `FakePanda` não tinha `get_video`.

Esses bugs são corrigidos nas Tasks 2, 3 e 4 respectivamente.

---

## Arquivos modificados

| Arquivo | Mudança |
|---|---|
| `src/models.py` | Adiciona `SP_MOVED = "sp_moved"` ao enum |
| `src/smartplayer_client.py` | Adiciona método `move_media` |
| `src/pipeline.py` | Remove prefixo de título; substitui bloco `SP_COMPLETED` por `SP_COMPLETED` + `SP_MOVED`; remove loop upfront de pastas em `run_pipeline` |
| `tests/test_models.py` | Adiciona teste do novo estado |
| `tests/test_smartplayer_client.py` | Corrige `test_create_media_returns_code`; adiciona `test_move_media` |
| `tests/test_pipeline_upload.py` | Corrige `FakeSP.create_media` para retornar `SPMedia`; adiciona `move_media`; atualiza asserções |
| `tests/test_pipeline_orchestrator.py` | Corrige `FakePanda` (adiciona `get_video`); corrige `FakeSP.create_media`; adiciona `move_media` |

---

## Task 1: Adicionar SP_MOVED ao VideoState

**Arquivos:**
- Modify: `src/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Escrever o teste que vai falhar**

Em `tests/test_models.py`, adicionar após `test_video_state_transitions_listed`:

```python
def test_sp_moved_state_exists():
    assert VideoState.SP_MOVED.value == "sp_moved"
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

```
pytest tests/test_models.py::test_sp_moved_state_exists -v
```

Esperado: `FAILED` com `AttributeError: SP_MOVED`

- [ ] **Step 3: Adicionar o estado ao enum**

Em `src/models.py`, linha 19 (entre `SP_COMPLETED` e `DONE`):

```python
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
    SP_MOVED = "sp_moved"
    DONE = "done"
    FAILED = "failed"
```

- [ ] **Step 4: Rodar o teste para confirmar que passa**

```
pytest tests/test_models.py -v
```

Esperado: todos os 3 testes `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: adicionar estado SP_MOVED ao VideoState"
```

---

## Task 2: Adicionar move_media ao SmartPlayerClient + corrigir test_create_media

**Arquivos:**
- Modify: `src/smartplayer_client.py`
- Test: `tests/test_smartplayer_client.py`

- [ ] **Step 1: Corrigir test_create_media_returns_code (bug pré-existente)**

Em `tests/test_smartplayer_client.py`, substituir o assert final de `test_create_media_returns_code`:

```python
# ANTES (linha ~148):
assert code == "media-xyz"
req = httpx_mock.get_request()
body = json.loads(req.content)
assert body == [{...}]

# DEPOIS (trocar a variável e o assert):
```

O teste completo corrigido fica assim:

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
        media = await c.create_media(
            name="Aula 01",
            description="Intro",
            external_id="panda-v1",
            total_size=12345,
        )

    assert media.code == "media-xyz"
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == [{
        "name": "Aula 01",
        "description": "Intro",
        "externalId": "panda-v1",
        "totalSize": 12345,
        "publicMedia": True,
    }]
```

- [ ] **Step 2: Rodar o teste corrigido**

```
pytest tests/test_smartplayer_client.py::test_create_media_returns_code -v
```

Esperado: `PASSED`

- [ ] **Step 3: Escrever o teste de move_media (vai falhar)**

No final de `tests/test_smartplayer_client.py`, adicionar (após `test_embed_url`):

```python
# ---------------------------------------------------------------------------
# Task 10 — move_media
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_move_media(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = _stub_token_cache(tmp_path)
    httpx_mock.add_response(
        method="PUT",
        url="https://services.scaleup.com.br/backoffice/v1/folders/moves",
        status_code=200,
        json={},
    )
    async with SmartPlayerClient("cid", "csec", "uc", cache) as c:
        await c.move_media("folder-abc", ["media-1", "media-2"])

    req = httpx_mock.get_request(method="PUT")
    assert req.headers["Authorization"] == "Bearer tok"
    body = json.loads(req.content)
    assert body == {
        "toFolderCode": "folder-abc",
        "mediaCodes": ["media-1", "media-2"],
        "folderCodes": [],
    }
```

- [ ] **Step 4: Rodar o teste para confirmar que falha**

```
pytest tests/test_smartplayer_client.py::test_move_media -v
```

Esperado: `FAILED` com `AttributeError: move_media`

- [ ] **Step 5: Implementar move_media no cliente**

Em `src/smartplayer_client.py`, adicionar após o método `poll_status` (antes de `build_embed_url`):

```python
@_retry_http()
async def move_media(self, folder_code: str, media_codes: list[str]) -> None:
    headers = await self._authed_headers()
    headers["Content-Type"] = "application/json"
    r = await self._client.put(
        f"{self._base_url}/folders/moves",
        headers=headers,
        json={"toFolderCode": folder_code, "mediaCodes": media_codes, "folderCodes": []},
    )
    r.raise_for_status()
```

- [ ] **Step 6: Rodar todos os testes do cliente SP**

```
pytest tests/test_smartplayer_client.py -v
```

Esperado: todos os 10 testes `PASSED`

- [ ] **Step 7: Commit**

```bash
git add src/smartplayer_client.py tests/test_smartplayer_client.py
git commit -m "feat: adicionar move_media ao SmartPlayerClient"
```

---

## Task 3: Atualizar upload_one + corrigir test_pipeline_upload

**Arquivos:**
- Modify: `src/pipeline.py` (upload_one)
- Test: `tests/test_pipeline_upload.py`

- [ ] **Step 1: Corrigir FakeSP no teste (bug pré-existente + add move_media)**

Em `tests/test_pipeline_upload.py`, substituir a classe `FakeSP` inteira:

```python
from src.smartplayer_client import SPMedia


class FakeSP:
    def __init__(self):
        self.created = []
        self.upload_calls = 0
        self.poll_calls = 0
        self.move_calls = []

    async def create_folder(self, name, parent_code=None):
        return f"sp-folder-{name[:3]}"

    async def create_media(self, name, description, external_id, total_size, public_media=True):
        self.created.append(external_id)
        return SPMedia(
            code=f"sp-media-{external_id}",
            status="DRAFT",
            urlsUpload={"urlUploadVideo": f"https://up/sp-media-{external_id}/v"},
        )

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

    async def move_media(self, folder_code, media_codes):
        self.move_calls.append((folder_code, media_codes))
```

- [ ] **Step 2: Atualizar as asserções do teste**

Substituir as asserções ao final de `test_upload_one_full_cycle`:

```python
    v = m.videos["v1"]
    assert v.state == VideoState.DONE
    assert v.sp_media_code == "sp-media-v1"
    assert v.sp_embed_url == "https://player.scaleup.com.br/embed/sp-media-v1"
    assert not Path(v.local_video_path).exists()  # cleanup
    assert sp.move_calls == [("sp-folder-EDU", ["sp-media-v1"])]
```

- [ ] **Step 3: Rodar o teste para confirmar que ainda falha (por causa do pipeline, não do fake)**

```
pytest tests/test_pipeline_upload.py -v
```

Esperado: `FAILED` — o `upload_one` ainda não tem o bloco `SP_MOVED` nem chama `move_media`

- [ ] **Step 4: Remover o prefixo de título em upload_one**

Em `src/pipeline.py`, substituir as linhas 75-79 de `upload_one`:

```python
# ANTES:
display_title = v.title
if " / " in v.panda_folder:
    # Usa caminho completo sem o prefixo "EDUCACIONAL | " (comum a todas as pastas)
    folder_path = v.panda_folder.split(" | ", 1)[-1]  # "Aceleração de Agências / Editadas"
    display_title = f"[{folder_path}] {v.title}"

# DEPOIS:
display_title = v.title
```

- [ ] **Step 5: Substituir o bloco SP_COMPLETED em upload_one**

Em `src/pipeline.py`, substituir o bloco `if v.state == VideoState.SP_COMPLETED:` (linhas 124-132):

```python
# ANTES:
if v.state == VideoState.SP_COMPLETED:
    if cleanup and v.local_video_path:
        try:
            Path(v.local_video_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("cleanup falhou para %s: %s", video_id, e)
        if v.local_thumb_path:
            Path(v.local_thumb_path).unlink(missing_ok=True)
    manifest.transition(video_id, VideoState.DONE)

# DEPOIS:
if v.state == VideoState.SP_COMPLETED:
    folder_code = await _ensure_sp_folder(sp, manifest, v.panda_folder)
    await sp.move_media(folder_code, [v.sp_media_code])
    manifest.transition(video_id, VideoState.SP_MOVED)

if v.state == VideoState.SP_MOVED:
    if cleanup and v.local_video_path:
        try:
            Path(v.local_video_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("cleanup falhou para %s: %s", video_id, e)
        if v.local_thumb_path:
            Path(v.local_thumb_path).unlink(missing_ok=True)
    manifest.transition(video_id, VideoState.DONE)
```

- [ ] **Step 6: Adicionar SP_MOVED nos imports do pipeline**

Em `src/pipeline.py`, garantir que `VideoState` é importado (já está). Verificar também que `SP_MOVED` está acessível — nenhuma mudança de import necessária.

- [ ] **Step 7: Rodar os testes de upload**

```
pytest tests/test_pipeline_upload.py -v
```

Esperado: `PASSED`

- [ ] **Step 8: Commit**

```bash
git add src/pipeline.py tests/test_pipeline_upload.py
git commit -m "feat: mover videos para pasta SP apos upload; remover prefixo de titulo"
```

---

## Task 4: Remover loop upfront de pastas + corrigir test_pipeline_orchestrator

**Arquivos:**
- Modify: `src/pipeline.py` (run_pipeline)
- Test: `tests/test_pipeline_orchestrator.py`

- [ ] **Step 1: Corrigir FakePanda (bug pré-existente) e FakeSP no teste do orquestrador**

Em `tests/test_pipeline_orchestrator.py`, substituir as duas classes inteiras:

```python
from src.smartplayer_client import SPMedia


class FakePanda:
    async def get_video(self, video_id):
        class _V:
            video_external_id = video_id
        return _V()

    async def request_download(self, video_id, quality, title):
        pass

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
        return SPMedia(
            code=f"m-{external_id}",
            status="DRAFT",
            urlsUpload={"urlUploadVideo": f"https://up/m-{external_id}/v"},
        )

    async def get_upload_urls(self, code):
        return {"urlUploadVideo": "https://up/v", "urlUploadPoster": "https://up/p"}

    async def upload_binary(self, url, file_path, content_type):
        pass

    async def poll_status(self, code):
        return "COMPLETED"

    async def move_media(self, folder_code, media_codes):
        pass
```

- [ ] **Step 2: Rodar o teste do orquestrador para confirmar que ainda falha (loop upfront ainda existe)**

```
pytest tests/test_pipeline_orchestrator.py -v
```

O teste pode passar neste ponto já (o loop upfront cria pastas, não impede nada). Se passar, ótimo — avançar para o step 3.

- [ ] **Step 3: Remover o loop upfront de criação de pastas em run_pipeline**

Em `src/pipeline.py`, dentro de `run_pipeline`, remover as 3 linhas:

```python
# REMOVER ESTAS LINHAS (atualmente por volta da linha 169-171):
# Garante pasta no SP para cada folder do manifest
for folder_name in list(manifest.folders.keys()):
    await _ensure_sp_folder(sp, manifest, folder_name)
```

O comentário e o loop devem ser completamente removidos.

- [ ] **Step 4: Adicionar SP_MOVED a pre_upload para garantir resumabilidade**

Em `src/pipeline.py`, dentro de `run_pipeline`, atualizar a tupla `pre_upload` para incluir `VideoState.SP_MOVED`:

```python
# ANTES:
pre_upload = (VideoState.DOWNLOADED, VideoState.SP_MEDIA_CREATED,
              VideoState.SP_UPLOAD_URLS_READY, VideoState.UPLOADING, VideoState.SP_PROCESSING,
              VideoState.SP_COMPLETED)

# DEPOIS:
pre_upload = (VideoState.DOWNLOADED, VideoState.SP_MEDIA_CREATED,
              VideoState.SP_UPLOAD_URLS_READY, VideoState.UPLOADING, VideoState.SP_PROCESSING,
              VideoState.SP_COMPLETED, VideoState.SP_MOVED)
```

Isso garante que se o processo for interrompido no estado `SP_MOVED`, a próxima execução de `run_pipeline` vai retomar do cleanup + DONE sem re-upload.

- [ ] **Step 5: Rodar todos os testes relevantes**

```
pytest tests/test_pipeline_upload.py tests/test_pipeline_orchestrator.py tests/test_models.py tests/test_smartplayer_client.py -v
```

Esperado: todos os testes `PASSED`

- [ ] **Step 6: Rodar a suite completa**

```
pytest -v
```

Esperado: todos os testes `PASSED`

- [ ] **Step 7: Commit**

```bash
git add src/pipeline.py tests/test_pipeline_orchestrator.py
git commit -m "refactor: criacao de pastas SP agora e lazy (sob demanda em upload_one)"
```
