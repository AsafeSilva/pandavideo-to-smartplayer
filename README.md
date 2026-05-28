# Migração Panda Video → SmartPlayer

> Requer Python 3.11+

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
# 1. Ver pastas disponíveis no Panda (opcional — para copiar nomes exatos)
python -m src.migrate list-folders

# 2. Descoberta (popula data/manifest.json)
# Por prefixo de nome:
python -m src.migrate discover --prefix "EDUCACIONAL |"
# Ou por nomes exatos de pastas:
python -m src.migrate discover --folder-names "Pasta A" "Pasta B"

# 3. Conferir o plano antes de baixar nada
python -m src.migrate run --dry-run

# 4. Executar migração
python -m src.migrate run

# 5. Em caso de falhas, reprocessar os que falharam
python -m src.migrate retry-failed

# 6. Gerar logs tabelados a qualquer momento
python -m src.migrate export

# 7. Limpar MP4s locais de vídeos já concluídos
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
- `RETRY_FAST=1` (apenas para testes — não usar em produção)

## Testes

```powershell
pytest -v
```
