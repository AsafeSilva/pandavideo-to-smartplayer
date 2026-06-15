# Disk Backpressure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforçar `MAX_DISK_USAGE_GB` no pipeline: workers de download pausam quando o espaço ocupado pelos `.mp4` em `data/downloads/` atingir o limite configurado.

**Architecture:** Função helper `_disk_used_gb` mede a soma dos `.mp4` em `download_dir`. O `download_worker` dentro de `run_pipeline` checa esse valor antes de cada download e dorme em loop de 0.01s (RETRY_FAST) ou 30s (produção) enquanto o limite estiver atingido. `migrate.py` passa `settings.max_disk_usage_gb or None` para `run_pipeline`.

**Tech Stack:** Python 3.11+, asyncio, pytest-asyncio, rich (dashboard)

---

## Mapa de arquivos

| Arquivo | Mudança |
|---|---|
| `src/pipeline.py` | Nova função `_disk_used_gb`, novo parâmetro `max_disk_gb` em `run_pipeline`, loop de espera em `download_worker` |
| `src/dashboard.py` | Novo ícone `"ag. disco..."` em `_PHASE_ICON` |
| `src/migrate.py` | Passa `max_disk_gb=settings.max_disk_usage_gb or None` nas duas chamadas a `run_pipeline` |
| `tests/test_pipeline_orchestrator.py` | Novo teste `test_disk_backpressure` |
| `CLAUDE.md` | Remove linha do Known Gap sobre `MAX_DISK_USAGE_GB` |

---

### Task 1: Helper `_disk_used_gb` com teste unitário

**Files:**
- Modify: `src/pipeline.py:1-17` (imports e topo do módulo)
- Test: `tests/test_pipeline_orchestrator.py`

- [ ] **Step 1: Escreva o teste unitário para `_disk_used_gb`**

Adicione ao final de `tests/test_pipeline_orchestrator.py`:

```python
def test_disk_used_gb_soma_mp4s(tmp_path: Path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "a.mp4").write_bytes(b"x" * 500)
    (dl / "b.mp4").write_bytes(b"x" * 500)
    (dl / "c.txt").write_bytes(b"x" * 9999)  # não deve ser contado

    from src.pipeline import _disk_used_gb
    result = _disk_used_gb(dl)

    assert abs(result - 1000 / (1024 ** 3)) < 1e-12


def test_disk_used_gb_pasta_vazia(tmp_path: Path):
    dl = tmp_path / "downloads"
    dl.mkdir()

    from src.pipeline import _disk_used_gb
    assert _disk_used_gb(dl) == 0.0


def test_disk_used_gb_pasta_inexistente(tmp_path: Path):
    from src.pipeline import _disk_used_gb
    # glob numa pasta inexistente não lança exceção — retorna 0
    assert _disk_used_gb(tmp_path / "nao_existe") == 0.0
```

- [ ] **Step 2: Execute os testes para confirmar que falham**

```
pytest tests/test_pipeline_orchestrator.py::test_disk_used_gb_soma_mp4s tests/test_pipeline_orchestrator.py::test_disk_used_gb_pasta_vazia tests/test_pipeline_orchestrator.py::test_disk_used_gb_pasta_inexistente -v
```

Esperado: `ImportError` ou `AttributeError` em `_disk_used_gb` (função não existe ainda).

- [ ] **Step 3: Implemente `_disk_used_gb` em `src/pipeline.py`**

Adicione logo após os imports (antes da linha `logger = logging.getLogger(__name__)`):

```python
def _disk_used_gb(download_dir: Path) -> float:
    if not download_dir.exists():
        return 0.0
    return sum(f.stat().st_size for f in download_dir.glob("*.mp4") if f.exists()) / (1024 ** 3)
```

- [ ] **Step 4: Execute os testes para confirmar que passam**

```
pytest tests/test_pipeline_orchestrator.py::test_disk_used_gb_soma_mp4s tests/test_pipeline_orchestrator.py::test_disk_used_gb_pasta_vazia tests/test_pipeline_orchestrator.py::test_disk_used_gb_pasta_inexistente -v
```

Esperado: 3 × PASSED.

- [ ] **Step 5: Commit**

```
git add src/pipeline.py tests/test_pipeline_orchestrator.py
git commit -m "feat: adicionar helper _disk_used_gb com testes unitários"
```

---

### Task 2: Backpressure no `download_worker`

**Files:**
- Modify: `src/pipeline.py:199-210` (assinatura de `run_pipeline`) e `src/pipeline.py:232-251` (`download_worker`)
- Test: `tests/test_pipeline_orchestrator.py`

- [ ] **Step 1: Escreva o teste de integração para o backpressure**

Adicione ao final de `tests/test_pipeline_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_disk_backpressure(tmp_path: Path, monkeypatch):
    """Verifica que downloads pausam quando disco cheio e retomam após upload liberar espaço."""
    monkeypatch.setenv("RETRY_FAST", "1")

    m = Manifest.load(tmp_path / "m.json")
    m.upsert_folder("F1", FolderEntry(panda_folder_id="f1"))
    for i in range(2):
        m.upsert_video(VideoEntry(
            panda_id=f"dv{i}", panda_folder="F1", title=f"DT{i}", size_bytes=100,
        ))
    m.save()

    # FakePanda.download_file escreve 100 bytes por vídeo.
    # Limite = 50 bytes → após o 1º download (100 bytes no disco), o 2º deve esperar
    # até que o upload do 1º delete o arquivo.
    limit_gb = 50 / (1024 ** 3)

    await run_pipeline(
        panda=FakePanda(),
        sp=FakeSP(),
        manifest=m,
        download_dir=tmp_path / "downloads",
        max_download_concurrency=1,
        max_upload_concurrency=1,
        poll_interval=0,
        max_disk_gb=limit_gb,
    )

    for i in range(2):
        assert m.videos[f"dv{i}"].state == VideoState.DONE, f"dv{i} não chegou a DONE"
    # Confirma que nenhum mp4 sobrou no disco
    assert list((tmp_path / "downloads").glob("*.mp4")) == []
```

- [ ] **Step 2: Execute o teste para confirmar que falha**

```
pytest tests/test_pipeline_orchestrator.py::test_disk_backpressure -v
```

Esperado: `TypeError: run_pipeline() got an unexpected keyword argument 'max_disk_gb'`

- [ ] **Step 3: Implemente o parâmetro `max_disk_gb` em `run_pipeline`**

Na assinatura de `run_pipeline` em `src/pipeline.py`, adicione o novo parâmetro após `limit`:

```python
async def run_pipeline(
    panda,
    sp,
    manifest: Manifest,
    download_dir: Path,
    max_download_concurrency: int = 3,
    max_upload_concurrency: int = 3,
    poll_interval: float = 30.0,
    quality: str = "original",
    limit: int | None = None,
    max_disk_gb: float | None = None,
    dashboard: "LiveDashboard | None" = None,
) -> None:
```

- [ ] **Step 4: Implemente o loop de espera no `download_worker`**

Dentro de `download_worker` em `src/pipeline.py`, logo após `if dashboard: dashboard.on_download_start(...)` e antes do `try:`, adicione:

```python
            if max_disk_gb is not None:
                _poll = 0.01 if os.environ.get("RETRY_FAST") == "1" else 30.0
                while _disk_used_gb(download_dir) >= max_disk_gb:
                    if dashboard:
                        dashboard.on_download_phase(vid, "ag. disco...")
                    logger.info(
                        "[disk] aguardando espaço — uso atual %.1f GB / %.0f GB",
                        _disk_used_gb(download_dir),
                        max_disk_gb,
                    )
                    await asyncio.sleep(_poll)
```

`import os` não existe em `src/pipeline.py`. Adicione junto aos outros imports padrão (após `import logging`):

Os imports no topo de `src/pipeline.py` ficam:

```python
import asyncio
import logging
import os
from pathlib import Path
```

O trecho completo do `download_worker` ficará assim (apenas a parte do loop + try):

```python
    async def download_worker():
        while True:
            vid = await to_download.get()
            if vid == sentinel:
                to_download.task_done()
                return
            v = manifest.videos[vid]
            size_mb = v.size_bytes / (1024 ** 2) if v.size_bytes else 0
            if dashboard:
                dashboard.on_download_start(vid, v.title or vid, size_mb)
            if max_disk_gb is not None:
                _poll = 0.01 if os.environ.get("RETRY_FAST") == "1" else 30.0
                while _disk_used_gb(download_dir) >= max_disk_gb:
                    if dashboard:
                        dashboard.on_download_phase(vid, "ag. disco...")
                    logger.info(
                        "[disk] aguardando espaço — uso atual %.1f GB / %.0f GB",
                        _disk_used_gb(download_dir),
                        max_disk_gb,
                    )
                    await asyncio.sleep(_poll)
            try:
                await download_one(panda, manifest, vid, download_dir, quality, poll_interval, dashboard=dashboard)
                await to_upload.put(vid)
            except Exception as e:
                manifest.mark_failed(vid, f"download: {e!r}")
                logger.exception("download falhou para %s", vid)
            finally:
                if dashboard:
                    dashboard.on_download_done(vid)
                to_download.task_done()
```

- [ ] **Step 5: Execute o teste para confirmar que passa**

```
pytest tests/test_pipeline_orchestrator.py::test_disk_backpressure -v
```

Esperado: PASSED.

- [ ] **Step 6: Execute a suíte completa para confirmar que nada quebrou**

```
pytest tests/test_pipeline_orchestrator.py -v
```

Esperado: todos os testes PASSED.

- [ ] **Step 7: Commit**

```
git add src/pipeline.py tests/test_pipeline_orchestrator.py
git commit -m "feat: enforçar MAX_DISK_USAGE_GB com backpressure no download_worker"
```

---

### Task 3: Dashboard, migrate.py e limpeza do CLAUDE.md

**Files:**
- Modify: `src/dashboard.py:18-27`
- Modify: `src/migrate.py:124-134` e `src/migrate.py:169-175`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Adicione o ícone `"ag. disco..."` em `src/dashboard.py`**

No dicionário `_PHASE_ICON` (linha 18), adicione a entrada após `"ag. Panda..."`:

```python
_PHASE_ICON = {
    "requisitando": "[cyan]↺[/]",
    "ag. Panda...": "[yellow]⌛[/]",
    "ag. disco...": "[yellow]💾[/]",
    "baixando...": "[cyan]⬇[/]",
    "concluído": "[green]✓[/]",
    "criando media": "[cyan]↺[/]",
    "enviando": "[cyan]↑[/]",
    "encoding...": "[yellow]⚙[/]",
    "DONE": "[green]✓[/]",
}
```

- [ ] **Step 2: Passe `max_disk_gb` na primeira chamada de `run_pipeline` em `src/migrate.py`**

Em `cmd_run` (linha ~124), a chamada a `run_pipeline` passa a ser:

```python
            await run_pipeline(
                panda=panda,
                sp=sp,
                manifest=manifest,
                download_dir=download_dir,
                max_download_concurrency=settings.max_download_concurrency,
                max_upload_concurrency=settings.max_upload_concurrency,
                quality=settings.panda_quality,
                limit=limit,
                max_disk_gb=settings.max_disk_usage_gb or None,
                dashboard=dashboard,
            )
```

- [ ] **Step 3: Passe `max_disk_gb` na segunda chamada de `run_pipeline` em `src/migrate.py`**

Em `cmd_retry_failed` (linha ~169), a chamada a `run_pipeline` passa a ser:

```python
            await run_pipeline(
                panda=panda, sp=sp, manifest=manifest, download_dir=download_dir,
                max_download_concurrency=settings.max_download_concurrency,
                max_upload_concurrency=settings.max_upload_concurrency,
                quality=settings.panda_quality,
                max_disk_gb=settings.max_disk_usage_gb or None,
                dashboard=dashboard,
            )
```

- [ ] **Step 4: Remova o Known Gap de `CLAUDE.md`**

Localize e remova a linha:

```
- `MAX_DISK_USAGE_GB` in `.env` is not enforced — check disk space manually before `run`
```

- [ ] **Step 5: Execute a suíte completa de testes**

```
pytest -v
```

Esperado: todos PASSED, nenhuma regressão.

- [ ] **Step 6: Commit**

```
git add src/dashboard.py src/migrate.py CLAUDE.md
git commit -m "feat: propagar max_disk_gb para run_pipeline e atualizar dashboard e docs"
```
