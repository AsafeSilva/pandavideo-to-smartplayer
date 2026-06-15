# Design: Backpressure por Uso de Disco no Pipeline

**Data:** 2026-05-28
**Status:** Aprovado

---

## 1. Contexto

`MAX_DISK_USAGE_GB` já existe em `config.py` e `.env.example` com default 10 GB, mas está listado como "Known Gap" no CLAUDE.md — a variável é carregada mas nunca usada para controlar o pipeline. Com downloads mais rápidos do que uploads, arquivos `.mp4` acumulam em `data/downloads/` sem limite, podendo encher o disco.

---

## 2. Objetivo

Enforçar `MAX_DISK_USAGE_GB`: quando o espaço ocupado pelos `.mp4` em `data/downloads/` atingir o limite, os workers de download pausam até que uploads concluídos liberem espaço.

---

## 3. Decisões de design

| Decisão | Escolha | Motivo |
|---|---|---|
| O que medir | Soma dos `.mp4` em `data/downloads/` | Simples, previsível, independe do disco do sistema |
| Comportamento ao atingir limite | Poll com `asyncio.sleep(30)` | Vídeos têm 1–2 GB, uploads levam minutos — 30s de lag é irrelevante |
| Feedback ao usuário | Log periódico + fase `"ag. disco..."` no dashboard | Visibilidade sem poluir o terminal |
| Como desativar | `MAX_DISK_USAGE_GB=0` → `None` internamente | Útil para testes e ambientes sem restrição |

---

## 4. Arquivos alterados

### `src/pipeline.py`

Nova função helper no topo do módulo:

```python
def _disk_used_gb(download_dir: Path) -> float:
    return sum(f.stat().st_size for f in download_dir.glob("*.mp4") if f.exists()) / (1024 ** 3)
```

`run_pipeline` ganha novo parâmetro:

```python
async def run_pipeline(
    ...
    max_disk_gb: float | None = None,
    ...
) -> None:
```

Loop de espera no `download_worker`, antes de chamar `download_one`:

```python
if max_disk_gb is not None:
    while _disk_used_gb(download_dir) >= max_disk_gb:
        if dashboard:
            dashboard.on_download_phase(vid, "ag. disco...")
        logger.info(
            "[disk] aguardando espaço — uso atual %.1f GB / %.0f GB",
            _disk_used_gb(download_dir), max_disk_gb,
        )
        await asyncio.sleep(30)
```

### `src/dashboard.py`

Adiciona ícone no dicionário `_PHASE_ICON`:

```python
"ag. disco...": "[yellow]💾[/]",
```

### `src/migrate.py`

Passa o novo parâmetro nas duas chamadas a `run_pipeline`:

```python
await run_pipeline(
    ...
    max_disk_gb=settings.max_disk_usage_gb or None,
)
```

O `or None` transforma `MAX_DISK_USAGE_GB=0` em `None`, desativando o limite.

---

## 5. Fluxo de backpressure

```
download A → [ag. disco...] ←———————————————————┐
download B → [ag. disco...]                      |
                                                 |
upload X → enviando → encoding → DONE → deleta ─┘
upload Y → enviando → encoding → DONE → deleta
```

Uploads rodam em paralelo e deletam o arquivo local ao chegar em `SP_MOVED`. O worker de download verifica o uso a cada 30s e retoma assim que houver espaço.

---

## 6. Testes

Novo teste em `tests/test_pipeline_orchestrator.py`:

- Pré-cria `.mp4` falsos em `download_dir` que somam mais que o `max_disk_gb` configurado
- Executa `run_pipeline` com `max_disk_gb` igual ao tamanho de um arquivo fake (ex: 1 byte = 1e-9 GB)
- O `FakePanda.download_file` cria arquivos pequenos (100 bytes)
- O `FakeSP.upload_binary` deleta o arquivo local ao ser chamado, simulando o cleanup real
- Confirma que o pipeline conclui todos os vídeos sem travar

Usa `RETRY_FAST=1` já existente para reduzir delays nos testes. O sleep de 30s é substituído por 0.01s quando `RETRY_FAST=1`.

---

## 7. Variáveis de ambiente

| Variável | Default | Descrição |
|---|---|---|
| `MAX_DISK_USAGE_GB` | `10` | Limite em GB para arquivos em `data/downloads/`. `0` desativa. |
