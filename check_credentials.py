"""Verifica se as credenciais do .env estão funcionando."""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from src.config import Settings
from src.panda_client import PandaClient
from src.smartplayer_client import SmartPlayerClient


async def check_panda(settings: Settings) -> bool:
    print("[ ] Testando Panda Video...")
    try:
        async with PandaClient(api_key=settings.panda_api_key) as c:
            folders = await c.list_folders()
        print(f"   [OK] Panda OK -- {len(folders)} pasta(s) encontrada(s)")
        for f in folders[:5]:
            print(f"      - {f.name}")
        if len(folders) > 5:
            print(f"      ... e mais {len(folders) - 5}")
        return True
    except Exception as e:
        print(f"   [ERRO] Panda FALHOU: {e}")
        return False


async def check_smartplayer(settings: Settings) -> bool:
    print("[ ] Testando SmartPlayer...")
    cache = Path("data/token_cache_test.json")
    try:
        async with SmartPlayerClient(
            client_id=settings.sp_client_id,
            client_secret=settings.sp_client_secret,
            user_code=settings.sp_user_code,
            token_cache_path=cache,
        ) as c:
            token = await c.get_token()
        print(f"   [OK] SmartPlayer OK -- token obtido ({token[:20]}...)")
        cache.unlink(missing_ok=True)
        return True
    except Exception as e:
        print(f"   [ERRO] SmartPlayer FALHOU: {e}")
        cache.unlink(missing_ok=True)
        return False


async def main():
    load_dotenv()
    try:
        settings = Settings.from_env()
    except SystemExit as e:
        print(f"❌ Credencial ausente: {e}")
        sys.exit(1)

    panda_ok = await check_panda(settings)
    sp_ok = await check_smartplayer(settings)

    print()
    if panda_ok and sp_ok:
        print("[OK] Tudo certo! Pode rodar: python -m src.migrate discover")
    else:
        print("[ERRO] Corrija as credenciais no .env antes de continuar.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
