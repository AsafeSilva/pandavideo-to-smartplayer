import json
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from src.smartplayer_client import SmartPlayerClient


# ---------------------------------------------------------------------------
# Task 7 — authentication / token cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_token_fetches_and_caches(httpx_mock: HTTPXMock, tmp_path: Path):
    httpx_mock.add_response(
        method="POST",
        url="https://services.scaleup.com.br/authentication/v1/oauth/token",
        json={"access_token": "tok-123", "expires_in": 604800, "token_type": "Bearer"},
    )
    cache = tmp_path / "token.json"
    async with SmartPlayerClient(
        client_id="cid",
        client_secret="csec",
        user_code="uc",
        token_cache_path=cache,
    ) as c:
        tok = await c.get_token()

    assert tok == "tok-123"
    assert cache.exists()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["access_token"] == "tok-123"
    assert "expires_at" in payload


@pytest.mark.asyncio
async def test_get_token_uses_cache_if_valid(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = tmp_path / "token.json"
    from datetime import datetime, timedelta, timezone
    cache.write_text(json.dumps({
        "access_token": "cached-tok",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }), encoding="utf-8")

    async with SmartPlayerClient(
        client_id="cid", client_secret="csec", user_code="uc",
        token_cache_path=cache,
    ) as c:
        tok = await c.get_token()

    assert tok == "cached-tok"
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_get_token_refreshes_when_near_expiry(httpx_mock: HTTPXMock, tmp_path: Path):
    cache = tmp_path / "token.json"
    from datetime import datetime, timedelta, timezone
    cache.write_text(json.dumps({
        "access_token": "old-tok",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
    }), encoding="utf-8")

    httpx_mock.add_response(
        method="POST",
        url="https://services.scaleup.com.br/authentication/v1/oauth/token",
        json={"access_token": "new-tok", "expires_in": 604800, "token_type": "Bearer"},
    )

    async with SmartPlayerClient(
        client_id="cid", client_secret="csec", user_code="uc",
        token_cache_path=cache,
    ) as c:
        tok = await c.get_token()

    assert tok == "new-tok"
