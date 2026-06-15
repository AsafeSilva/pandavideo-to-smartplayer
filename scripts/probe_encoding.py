"""
Probe de diagnóstico: baixa um vídeo do Panda, sobe pro SP e loga a resposta
completa do polling de encoding — incluindo o body quando status = ERROR.

Uso:
    python probe_encoding.py
"""
import asyncio
import json
import logging
import tempfile
from pathlib import Path

import httpx

from src.config import Settings
from src.panda_client import PandaClient
from src.smartplayer_client import SmartPlayerClient

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger("probe")

# Vídeo alvo: "1- Comece por aqui" (45.6 MB / 53s)
PANDA_ID        = "73eb4aee-8804-4e42-a957-4bf61f5d189f"
PANDA_EXT_ID    = "7d5356db-e29e-44f6-8462-b4b7739d90ef"
VIDEO_TITLE     = "PROBE - 1- Comece por aqui"
VIDEO_SIZE      = 45_670_000   # aprox, será atualizado

TOKEN_CACHE     = Path("data/.sp_token_cache.json")
DOWNLOAD_DIR    = Path("data/probe_downloads")
POLL_INTERVAL   = 10   # segundos entre polls


async def download_from_panda(panda: PandaClient, panda_id: str, dest: Path) -> int:
    log.info("Solicitando download async do Panda para %s ...", panda_id)
    url = await panda.request_download(panda_id)
    log.info("URL de download: %s", url)

    log.info("Baixando arquivo ...")
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30.0)) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            total = 0
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes(1024 * 1024):
                    f.write(chunk)
                    total += len(chunk)
    log.info("Download concluído: %.1f MB", total / 1_048_576)
    return total


async def main():
    settings = Settings.from_env()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local_path = DOWNLOAD_DIR / f"{PANDA_ID}.mp4"

    async with (
        PandaClient(api_key=settings.panda_api_key) as panda,
        SmartPlayerClient(
            client_id=settings.sp_client_id,
            client_secret=settings.sp_client_secret,
            user_code=settings.sp_user_code,
            token_cache_path=TOKEN_CACHE,
        ) as sp,
    ):
        # 1. Download do Panda (pula se já existe)
        if local_path.exists():
            size = local_path.stat().st_size
            log.info("Arquivo já existe localmente (%.1f MB), pulando download.", size / 1_048_576)
        else:
            size = await download_from_panda(panda, PANDA_ID, local_path)

        # 2. Criar mídia no SP
        log.info("Criando mídia no SP ...")
        media = await sp.create_media(
            name=VIDEO_TITLE,
            description="probe diagnóstico",
            external_id=f"probe-{PANDA_EXT_ID}",
            total_size=size,
        )
        log.info("Mídia criada: code=%s  status=%s", media.code, media.status)
        log.info("urlsUpload: %s", json.dumps(media.urlsUpload, indent=2) if media.urlsUpload else "VAZIO")

        if not media.urlsUpload:
            log.error("SP não retornou URLs de upload — abortando.")
            return

        # 3. Upload do arquivo
        upload_url = next(iter(media.urlsUpload.values()))
        log.info("Fazendo upload para SP (%.1f MB) ...", size / 1_048_576)
        await sp.upload_binary(upload_url, local_path, "video/mp4")
        log.info("Upload concluído.")

        # 4. Polling do status com body completo
        log.info("Iniciando polling de encoding (a cada %ds) ...", POLL_INTERVAL)
        poll_count = 0
        while True:
            poll_count += 1

            # Chamada raw para ver o body completo independente do status
            headers = await sp._authed_headers()
            r = await sp._client.get(
                f"{sp._base_url}/medias/{media.code}",
                headers=headers,
            )
            body = r.json()
            status = body.get("status", "UNKNOWN")
            log.info("[poll #%d] status=%s | body=%s", poll_count, status, json.dumps(body, ensure_ascii=False))

            if status == "COMPLETED":
                log.info("Encoding CONCLUÍDO com sucesso!")
                break
            elif status == "ERROR":
                log.error("Encoding FALHOU — veja o body acima para diagnóstico.")
                break
            elif poll_count > 60:
                log.warning("Timeout: mais de 60 polls sem conclusão. Encerrando.")
                break

            await asyncio.sleep(POLL_INTERVAL)

        # 5. Limpeza
        if local_path.exists():
            local_path.unlink()
            log.info("Arquivo local removido.")


if __name__ == "__main__":
    asyncio.run(main())
