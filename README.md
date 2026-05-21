# Migração Panda Video → SmartPlayer

> Requer Python 3.11+

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
