# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python CLI tool that migrates videos from Panda Video to SmartPlayer, preserving folder structure and metadata. One-shot migration — not a sync service.

## Commands

```powershell
# Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env   # then fill in credentials

# Run tests
pytest -v

# Migration workflow
python -m src.migrate list-folders                                        # lista pastas disponíveis no Panda
python -m src.migrate discover --prefix "EDUCACIONAL |"                  # descobre por prefixo
python -m src.migrate discover --folder-names "Pasta A" "Pasta B"        # descobre por nome exato
python -m src.migrate run --dry-run
python -m src.migrate run
python -m src.migrate retry-failed
python -m src.migrate export
python -m src.migrate cleanup
```

## Architecture

Six focused modules under `src/`:

| Module | Responsibility |
|---|---|
| `models.py` | `VideoState` enum (11 states), `VideoEntry`, `FolderEntry` dataclasses |
| `manifest.py` | Persistent state machine in `data/manifest.json` — atomic write, the only truth |
| `panda_client.py` | Async HTTP wrapper for Panda Video API; auth via bare API key (no Bearer) |
| `smartplayer_client.py` | Async HTTP wrapper for SmartPlayer; OAuth2 token with 7-day cache |
| `pipeline.py` | `download_one`, `upload_one`, `run_pipeline` — asyncio workers + queues |
| `discovery.py` | Lists Panda folders/videos and populates manifest |
| `exporter.py` | Generates `migration_log.md` (grouped) and `migration_log.csv` (flat) |
| `config.py` | Loads `.env` → `Settings` dataclass, fails early on missing required vars |
| `logging_setup.py` | JSON-lines formatter + configure_logging() |
| `migrate.py` | CLI entry point (`python -m src.migrate`) — routes to async command functions |

## Key Design Decisions

- **Manifest is source of truth**: every state change calls `manifest.transition()` which saves atomically. The pipeline is fully resumable — just re-run.
- **No upload by URL**: SmartPlayer doesn't support it. Flow: Panda async download → local MP4 → SP upload. MP4s are deleted after `DONE`.
- **Retry pattern**: `RETRY_FAST=1` env var uses millisecond delays for tests; production uses 4-30s backoff. Set in `_retry_http()` in both clients.
- **Auth**: Panda = `Authorization: <key>` (no Bearer). SmartPlayer = `Authorization: Bearer <token>` + `X-User-Code`.

## Environment Variables

See `.env.example`. Required: `PANDA_API_KEY`, `SMARTPLAYER_CLIENT_ID`, `SMARTPLAYER_CLIENT_SECRET`, `SMARTPLAYER_USER_CODE`.

Commands `export` and `cleanup` work without credentials. All others require `.env`.

## Workflow de desenvolvimento

Ao finalizar qualquer alteração no código, rodar os testes e perguntar ao usuário se deseja commitar antes de encerrar a sessão. Nunca commitar ou fazer push sem confirmação explícita — o usuário pode querer fazer testes adicionais ou ajustes antes.

```powershell
pytest -v                        # rodar antes de propor o commit
git add <arquivos alterados>
git commit -m "tipo: descrição"
```

## Scripts de diagnóstico

Scripts utilitários e de diagnóstico ficam em `scripts/`. **Nunca criar scripts avulsos na raiz do projeto.**

| Script | Propósito |
|---|---|
| `check_manifest.py` | Inspeciona estado do manifest |
| `check_duplicates.py` | Detecta vídeos duplicados |
| `check_depoimentos.py` | Verifica pasta de depoimentos |
| `check_panda_storage.py` | Checa uso de storage no Panda |
| `probe_sp_api.py` | Probe genérico da API do SmartPlayer |
| `probe_encoding.py` | Diagnóstico de encoding: baixa vídeo do Panda, sobe pro SP e loga resposta completa |
| `probe_sp_delete.py` | Deleta mídias no SP via API |
| `probe_sp_failed.py` | Consulta status atual dos vídeos FAILED no SP (body JSON completo) |
| `reset_failed.py` | Reseta vídeos failed no manifest |
| `reset_failed_sp_cleanup.py` | Deleta mídias FAILED no SP e reseta manifest para PENDING |
| `rediscover_folder.py` | Re-descobre vídeos de uma pasta pelo panda_folder_id |

## Known Gaps

- Thumbnail download is not implemented (SmartPlayer auto-generates from video frames)
- `--resume` flag exists but is a no-op (manifest always resumes automatically)
