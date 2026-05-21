# Migração de Vídeos Panda Video → SmartPlayer

**Data:** 2026-05-21
**Autor:** Asafe Silva (Expert Integrado)
**Status:** Design aprovado, pronto para plano de implementação

---

## 1. Contexto e objetivo

Migrar vídeos hospedados no Panda Video para o SmartPlayer (Scaleup), preservando estrutura de pastas, metadados (título, descrição, thumbnail, tags, duração) e gerando uma planilha de mapeamento entre IDs/URLs antigos e novos. O caso de uso principal é alimentar uma plataforma em construção que consumirá os embeds do SmartPlayer.

**Escopo inicial:** pastas com prefixo `EDUCACIONAL |` no Panda (6 pastas, ~51 vídeos). O script é parametrizável para outras pastas/prefixos no futuro.

**Não-objetivo:** sincronização contínua. Esta é uma migração one-shot, executada manualmente sob supervisão.

---

## 2. Restrições descobertas nas APIs

### Panda Video ([docs](https://docs.pandavideo.com))
- Auth: API Key no header `Authorization` (sem prefixo `Bearer`). Sem expiração, revogável manualmente. Obtém-se em **Configurações → Avançado → Gerar nova chave API** (requer permissão de admin/owner)
- Base URL: `https://api-v2.pandavideo.com.br`
- Listagem de pastas: `GET /folders?parent_folder_id=...` (recursivo, sem paginação documentada)
- Listagem de vídeos: `GET /videos?folder_id=...&page=N&limit=100` (paginado)
- Metadados de vídeo: `GET /videos/{id}` retorna `title`, `description`, `folder_id`, `thumbnail`, `length`, `size`, `tags[]`, `created_at`, `status`
- Download: fluxo assíncrono em 3 etapas
  1. `POST /download-async/{video_id}` com `{quality, format, language, video_title}`
  2. `GET /download-async/{id}/{format}/{quality}/{language}` polling (200 = pronto, 400 = processando)
  3. Resposta 200 traz URL S3 pré-assinada com validade curta — baixar imediatamente
- Qualidade escolhida: `original` (preserva master, sem reencode duplo)
- Sem rate limit documentado — usar backoff exponencial em 429/5xx

### SmartPlayer / Scaleup ([docs](https://smartplayer.readme.io))
- Auth: OAuth2 `client_credentials` em `POST https://services.scaleup.com.br/authentication/v1/oauth/token` (token válido 7 dias)
- Header obrigatório: `Authorization: Bearer <token>` + `X-User-Code: <user_code>`
- Base URL: `https://services.scaleup.com.br/backoffice/v1` (folders list usa `/v2`)
- Folders: `POST /folders` com body `{name}` + query opcional `root-folder-code`; `GET /v2/folders` lista
- **Não há upload por URL** — o fluxo é obrigatoriamente:
  1. `POST /medias` com array de até 50 itens — retorna `code` e `status: DRAFT`
  2. `GET /medias/{code}` — retorna `urlsUpload.urlUploadVideo` e `urlsUpload.urlUploadPoster`
  3. `PUT <urlUploadVideo>` com `Content-Type: video/mp4` e body binário
  4. (opcional) `PUT <urlUploadPoster>` com a thumbnail
  5. Polling `GET /medias/{code}` em `status` até `COMPLETED` (lifecycle: DRAFT → UPLOADED → EXTRACT_METADATA → EXTRACT_POSTER_THUMB → UPLOADED_POSTER → COMPRESS_ENCODE → UPLOAD_UPDATE → COMPLETED)
- Embed URL final: `https://player.scaleup.com.br/embed/{code}`
- Sem webhooks documentados — usar polling
- Limite de batch: 50 itens em endpoints de medias

### Implicações de design
- Migração precisa ser `Panda → disco local → SmartPlayer` (sem atalho via URL)
- Dois pollings longos (Panda download + SP encoding) tornam fluxo serial muito lento
- Token SP precisa de refresh automático (7 dias)
- Estado precisa ser persistente: falha em qualquer etapa não pode obrigar a refazer tudo

---

## 3. Decisões de arquitetura

| Decisão | Escolha | Por quê |
|---|---|---|
| Linguagem | Python 3.11+ | Job one-shot I/O-bound. `asyncio + httpx` para concorrência, `tqdm` para CLI, ecossistema maduro para scripts. Panda não tem SDK Node, então JS não economizaria meio caminho |
| Concorrência | `asyncio` com 2 pools de workers | 3 downloaders + 3 uploaders separados, ligados por fila — aproveita banda durante polling longo |
| Persistência de estado | `manifest.json` (file-based) | Sem dependência de DB. Atômico via write-temp + rename. Permite `--resume` |
| Qualidade do download | `original` | Preserva master; SP fará encoding próprio. Evita perda por re-encode duplo |
| Cleanup de MP4s locais | Apagar após status `COMPLETED` no SP | Disco enxuto. Limite `MAX_DISK_USAGE_GB` pausa novos downloads se exceder |
| Destino dos downloads | `data/downloads/` dentro do próprio projeto | Mantém tudo num lugar só, simplifica setup. Cleanup automático garante que o pico fica em `MAX_DISK_USAGE_GB` (default 10 GB) |
| Isolamento Python | `venv` obrigatório | Dependências (`httpx`, `pydantic`, `tenacity`, `tqdm`) ficam isoladas. `python -m venv .venv` builtin, sem libs extras |
| Filtro de pastas | Prefixo `EDUCACIONAL \|` por padrão, configurável via CLI | Permite reuso para outras migrações no futuro |
| Output | Markdown agrupado por pasta + CSV plano | MD para revisão humana, CSV para import em Sheets/Excel |
| Estimativa prévia | Discovery coleta `size_bytes` do Panda antes de qualquer download | `--dry-run` mostra GB totais e maior arquivo, sem efeito colateral |

---

## 4. Arquitetura de componentes

```
┌─────────────────────────────────────────────────────────┐
│                     migrate.py (CLI)                    │
│   discover | run | dry-run | resume | retry-failed |    │
│   export | cleanup                                       │
└──────────────┬──────────────────────────────────────────┘
               │
   ┌───────────▼────────────┐
   │  Discovery (sequencial)│  lista pastas Panda → filtra prefixo → lista vídeos → grava no manifest
   └───────────┬────────────┘
               │
   ┌───────────▼────────────────────────────────────────┐
   │           Manifest (data/manifest.json)             │
   │  estado por vídeo: pending → downloading → ...      │
   └────┬─────────────────────────────────────────┬──────┘
        │                                         │
  ┌─────▼──────────────┐                ┌────────▼─────────┐
  │  Download Workers  │   asyncio.Queue│  Upload Workers  │
  │  (3 concorrentes)  │────────────────▶  (3 concorrentes)│
  │  Panda → disco     │   "ready files"│  disco → SP      │
  └────────────────────┘                └────────┬─────────┘
                                                  │
                                       ┌──────────▼─────────┐
                                       │  Cleanup           │
                                       │  apaga MP4 após    │
                                       │  status COMPLETED  │
                                       └──────────┬─────────┘
                                                  │
                                       ┌──────────▼─────────┐
                                       │  Export (md + csv) │
                                       └────────────────────┘
```

### Módulos

| Módulo | Responsabilidade | Contrato público (interface) |
|---|---|---|
| `src/panda_client.py` | Wrapper HTTP da API Panda Video | `list_folders()`, `list_videos(folder_id)`, `get_video(id)`, `request_download(id, quality)`, `poll_download(id, quality)`, `download_file(url, dest_path)` |
| `src/smartplayer_client.py` | Wrapper HTTP da API SmartPlayer | `get_token()` (cache + refresh), `list_folders()`, `create_folder(name, parent_code)`, `create_media(payload)`, `get_upload_urls(code)`, `upload_binary(url, path, content_type)`, `poll_status(code)` |
| `src/manifest.py` | State machine persistente em JSON | `load()`, `save()`, `upsert_folder()`, `upsert_video()`, `transition(video_id, new_state, **fields)`, `videos_in_state(state)`, `pending_for_folder(folder)` |
| `src/exporter.py` | Geração de output tabelado | `export_markdown(path)`, `export_csv(path)` |
| `src/migrate.py` | Orquestrador / CLI | Comandos: `discover`, `run`, `retry-failed`, `export`, `cleanup` |

Cada módulo é importável e testável isoladamente. `panda_client` e `smartplayer_client` não conhecem `manifest` — o orquestrador (`migrate.py`) é quem amarra os dois.

### Diretórios

```
videos-educacional/
├── .env.example
├── .env                       (gitignored)
├── .gitignore
├── requirements.txt
├── README.md
├── docs/
│   └── superpowers/specs/2026-05-21-panda-to-smartplayer-migration-design.md
├── src/
│   ├── __init__.py
│   ├── panda_client.py
│   ├── smartplayer_client.py
│   ├── manifest.py
│   ├── exporter.py
│   └── migrate.py
├── data/
│   ├── manifest.json          (state)
│   ├── token_cache.json       (token SP + expiração)
│   ├── downloads/             (gitignored — MP4s temporários)
│   ├── migration_log.md       (output humano)
│   └── migration_log.csv      (output planilha)
└── logs/
    └── migration_YYYYMMDD_HHMMSS.log
```

---

## 5. State machine por vídeo

Cada vídeo no `manifest.json` percorre estados explícitos. Cada transição grava o manifest **antes** de tentar a próxima ação — idempotência total em retomadas.

```
                    pending
                       │
                       ▼
              download_requested ──(POST /download-async no Panda)
                       │
                       ▼
              download_ready ──(polling status até 200 + URL S3)
                       │
                       ▼
              downloaded ──(MP4 + thumb gravados em data/downloads/)
                       │
                       ▼
              sp_media_created ──(POST /medias no SP → code DRAFT)
                       │
                       ▼
              sp_upload_urls_ready ──(GET /medias/{code} retornou urlUploadVideo)
                       │
                       ▼
              uploading ──(PUT binário para urlUploadVideo + urlUploadPoster)
                       │
                       ▼
              sp_processing ──(polling status: UPLOADED → ... → COMPLETED)
                       │
                       ▼
              sp_completed ──(embed URL pronta; MP4 local apagado)
                       │
                       ▼
                     done

  qualquer estado pode ir para: failed (com error + retry_count ≤ 3)
```

**Mapeamento de pastas:** antes de processar o primeiro vídeo de uma pasta, o orquestrador chama `POST /folders` no SP e grava o `sp_folder_code` no manifest, indexado pelo nome original. Pastas reutilizam o code em execuções futuras (idempotente).

**Schema do manifest:**

```json
{
  "discovered_at": "2026-05-21T13:42:18Z",
  "folders": {
    "EDUCACIONAL | Mestres do ChatGPT": {
      "panda_folder_id": "abc-123",
      "sp_folder_code": "sp-folder-xyz"
    }
  },
  "videos": {
    "panda-uuid-456": {
      "panda_id": "panda-uuid-456",
      "panda_folder": "EDUCACIONAL | Mestres do ChatGPT",
      "title": "Aula 01 - Introdução",
      "description": "...",
      "thumbnail_url": "https://b-vz-...panda.../thumb.jpg",
      "duration_sec": 1834,
      "size_bytes": 524288000,
      "tags": ["chatgpt", "intro"],
      "state": "sp_processing",
      "local_video_path": "data/downloads/panda-uuid-456.mp4",
      "local_thumb_path": "data/downloads/panda-uuid-456.jpg",
      "sp_media_code": "ea6531ab6585...",
      "sp_embed_url": null,
      "retry_count": 0,
      "last_error": null,
      "created_at": "2026-05-21T13:42:18Z",
      "updated_at": "2026-05-21T13:51:02Z"
    }
  }
}
```

---

## 6. Concorrência, erros e retry

### Workers
- 3 workers de download consumindo da fila `to_download`
- 3 workers de upload consumindo da fila `to_upload`
- Workers de download empurram para `to_upload` ao concluir
- Limite de disco: `MAX_DISK_USAGE_GB=10` (default) — se atingido, downloads pausam até cleanup liberar espaço

### Política de retry (via `tenacity`)
- 3 tentativas por transição de estado, backoff exponencial: 30s → 2min → 8min
- Retry dispara em: timeout, conexão recusada, 5xx, 429
- 4xx (exceto 429) marca `failed` imediatamente (não adianta repetir payload errado)
- Polling do download Panda: intervalo 30s, timeout máximo 60min por vídeo
- Polling do encoding SP: intervalo 60s, timeout máximo 2h por vídeo
- Falha registrada em `last_error` (string) + traceback completo nos logs JSON

### Refresh de token SP
- Token cacheado em `data/token_cache.json` com `expires_at`
- A cada chamada autenticada, verifica `expires_at - 5min`; se vencido, refresh
- 401 dispara refresh forçado + 1 retry da request original

---

## 7. CLI

```bash
# Descoberta — popula manifest sem baixar nada
python -m src.migrate discover --prefix "EDUCACIONAL |"

# Dry-run — mostra plano (quantos vídeos, GB estimados, chamadas de API)
python -m src.migrate run --dry-run

# Execução real
python -m src.migrate run

# Retoma sem refazer discovery
python -m src.migrate run --resume

# Reprocessa só os falhos (state = failed)
python -m src.migrate retry-failed

# Gera output final a partir do manifest
python -m src.migrate export

# Limpa downloads locais de vídeos com state = done
python -m src.migrate cleanup
```

**Flags globais:**
- `--prefix TEXT` (default `"EDUCACIONAL |"`) — filtro de nome de pasta
- `--folders ID...` — sobrescreve `--prefix` com IDs específicos
- `--quality {240p|360p|480p|720p|1080p|2160p|original}` (default `original`)
- `--max-concurrency N` (default 3) — aplicado igualmente a downloaders e uploaders
- `--max-disk-gb N` (default 10)
- `--log-level {DEBUG|INFO|WARNING|ERROR}` (default INFO)

---

## 8. Configuração (.env)

```
# Panda Video — obter em Configurações → Avançado → Gerar nova chave API
PANDA_API_KEY=

# SmartPlayer — credenciais OAuth2 já em mãos
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

`python-dotenv` carrega no startup. Valores ausentes geram erro claro (`raise SystemExit("PANDA_API_KEY ausente em .env")`) antes de qualquer chamada HTTP.

---

## 9. Output final

### `data/migration_log.md` (humano, agrupado por pasta)

```markdown
# Migração Panda Video → SmartPlayer

Executada em: 2026-05-21 14:32 BRT
Vídeos descobertos: 51 | Sucesso: 49 | Falhas: 2

## EDUCACIONAL | Mestres do ChatGPT (31 vídeos)

| # | Título | Panda ID | Panda URL | SP Code | Embed URL | Status |
|---|--------|----------|-----------|---------|-----------|--------|
| 1 | Aula 01 - Introdução | panda-uuid-456 | https://app.pandavideo.com/videos/panda-uuid-456 | ea6531ab... | https://player.scaleup.com.br/embed/ea6531ab... | done |
| 2 | Aula 02 - Prompting | panda-uuid-457 | ... | — | — | failed: upload timeout após 3 tentativas |
...
```

### `data/migration_log.csv` (plano, para Excel/Sheets)

Colunas: `pasta, titulo, panda_id, panda_url, panda_thumbnail, duracao_segundos, size_mb, sp_media_code, sp_embed_url, status, erro, executado_em`

### `logs/migration_*.log` (JSON-lines)

```json
{"ts":"2026-05-21T14:32:01.234Z","level":"INFO","video_id":"panda-uuid-456","state":"downloaded","action":"transition","duration_ms":182000,"size_bytes":524288000}
```

Facilita `grep`/`jq` para análise pós-execução.

---

## 10. Riscos e mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Token SP expira durante upload longo | Média | Refresh proativo 5min antes de `expires_at`; 401 dispara refresh + retry |
| URL S3 do Panda expira antes do download terminar | Baixa | URL é gerada imediatamente antes do download; download usa connection reuse e timeout amplo |
| Disco lota durante migração | Média | Limite `MAX_DISK_USAGE_GB` pausa downloads; cleanup roda assim que cada vídeo completa |
| Mudança de schema na API mid-flight | Baixa | `pydantic` valida respostas — erro claro em vez de bug silencioso |
| SP rejeita upload por tamanho excessivo | Média | Limite não documentado. Dry-run mostra maior arquivo previsto; tratar erro de upload com mensagem explícita |
| Falha de rede prolongada | Média | `--resume` retoma do último estado salvo; retries automáticos cobrem falhas curtas |
| Vídeo com `status != converted` no Panda | Baixa | Discovery filtra apenas vídeos `converted`; outros são listados em `data/skipped.txt` |

---

## 11. Pendências externas ao código

1. **Obter `PANDA_API_KEY`** — depende de acesso de admin/owner ao painel Panda. Ver Configurações → Avançado.
2. **Confirmar `SMARTPLAYER_USER_CODE`** — já temos `client_id`/`client_secret`; confirmar que o `userCode` está em mãos.
3. **Validar com suporte SmartPlayer** (se aparecer erro no upload): limite de tamanho por arquivo e método HTTP exato do upload binário (a doc não confirma se é PUT ou POST multipart).

Nenhuma dessas bloqueia o desenvolvimento do script — só a execução final.

### Setup inicial (uma vez)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # depois preencher os valores
```

---

## 12. Critérios de sucesso

- [ ] `python -m src.migrate discover --prefix "EDUCACIONAL |"` popula `manifest.json` com todas as pastas e vídeos esperados (~51)
- [ ] `python -m src.migrate run --dry-run` imprime plano fiel sem efeitos colaterais
- [ ] `python -m src.migrate run` migra com sucesso ≥ 95% dos vídeos em uma única execução
- [ ] Após interrupção forçada (Ctrl+C) no meio da execução, `python -m src.migrate run --resume` continua do último estado consistente sem re-baixar/re-upar nada já concluído
- [ ] `data/migration_log.md` e `data/migration_log.csv` contêm título, IDs antigo e novo, e embed URL para todo vídeo com `state = done`
- [ ] Disco volta a < 1GB ocupado em `data/downloads/` após `cleanup`
