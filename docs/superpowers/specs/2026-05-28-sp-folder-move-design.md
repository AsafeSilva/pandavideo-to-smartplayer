# Design: Move automático de vídeos para pastas no SmartPlayer

**Data:** 2026-05-28  
**Status:** Aprovado

## Contexto

Durante a migração Panda → SmartPlayer, a API do SP não tinha documentação clara sobre como mover mídias para pastas. Como workaround, o pipeline passou a prefixar o título de cada vídeo com o caminho da pasta (`[Aceleração de Agências / Editadas] Título do Vídeo`) para facilitar organização manual posterior.

A documentação do endpoint de move foi obtida e o workaround pode ser eliminado.

## Objetivo

Automatizar o posicionamento de cada vídeo na pasta correta do SmartPlayer durante o pipeline, removendo o workaround de prefixo no título.

## Decisões de design

- **Novo estado `SP_MOVED`** entre `SP_COMPLETED` e `DONE`: torna o pipeline totalmente resumável. Se o move falhar, na próxima execução o vídeo vai direto para a etapa de move sem re-upload.
- **Move individual** (um vídeo por chamada): mais simples e imediato. O endpoint aceita batch, mas mover logo após cada `SP_COMPLETED` elimina dependência de aguardar outros vídeos da mesma pasta.
- **Criação de pastas lazy**: pastas são criadas sob demanda dentro de `upload_one`, não mais todas de uma vez no início de `run_pipeline`. O manifest continua como cache — se o código já estiver salvo, nenhuma chamada de API é feita.
- **Confiança no manifest**: não há verificação se a pasta existe no SP antes de criar. O manifest é fonte de verdade. Para uma migração one-shot isso é aceitável.

## Novo fluxo de estados

```
PENDING
  → DOWNLOAD_REQUESTED
  → DOWNLOAD_READY
  → DOWNLOADED
  → SP_MEDIA_CREATED
  → SP_UPLOAD_URLS_READY  (fallback)
  → UPLOADING
  → SP_PROCESSING
  → SP_COMPLETED
  → SP_MOVED              ← novo
  → DONE
  → FAILED                (qualquer etapa)
```

## Mudanças por arquivo

### `src/models.py`

Adicionar `SP_MOVED = "sp_moved"` ao enum `VideoState`, entre `SP_COMPLETED` e `DONE`.

### `src/smartplayer_client.py`

Adicionar método `move_media`:

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

Endpoint: `PUT https://services.scaleup.com.br/backoffice/v1/folders/moves`

### `src/pipeline.py`

**1. Remover prefixo do título em `upload_one`** (linhas 76-79):

```python
# ANTES
display_title = v.title
if " / " in v.panda_folder:
    folder_path = v.panda_folder.split(" | ", 1)[-1]
    display_title = f"[{folder_path}] {v.title}"

# DEPOIS
display_title = v.title  # título limpo, sem prefixo de pasta
```

**2. Adicionar bloco `SP_MOVED` em `upload_one`** após o bloco `SP_COMPLETED`:

```python
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

O bloco de cleanup existente (atualmente dentro de `SP_COMPLETED`) é movido para dentro do bloco `SP_MOVED`.

**3. Remover loop upfront de criação de pastas em `run_pipeline`** (linhas 169-171):

```python
# REMOVER:
for folder_name in list(manifest.folders.keys()):
    await _ensure_sp_folder(sp, manifest, folder_name)
```

### `src/manifest.py`

Nenhuma mudança necessária. `transition()` e `upsert_folder()` já suportam o novo estado e o fluxo lazy.

## Impacto em testes existentes

Testes que verificam o estado `DONE` após `SP_COMPLETED` precisarão ser ajustados para incluir o estado intermediário `SP_MOVED`. Mocks do `SmartPlayerClient` precisarão incluir `move_media`.
