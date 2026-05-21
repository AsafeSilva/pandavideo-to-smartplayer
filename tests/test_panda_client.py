"""Testes para PandaClient."""
import pytest
from pytest_httpx import HTTPXMock

from src.panda_client import PandaClient


# ---------------------------------------------------------------------------
# Task 4 — list_folders e list_videos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_folders_returns_parsed(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        json={"folders": [
            {"id": "f1", "name": "EDUCACIONAL | LIVES", "parent_folder_id": None},
            {"id": "f2", "name": "AREA COMERCIAL", "parent_folder_id": None},
        ]},
    )
    async with PandaClient(api_key="fake-key") as c:
        folders = await c.list_folders()

    assert len(folders) == 2
    assert folders[0].id == "f1"
    assert folders[0].name == "EDUCACIONAL | LIVES"


@pytest.mark.asyncio
async def test_list_folders_sends_auth_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        json={"folders": []},
    )
    async with PandaClient(api_key="my-secret") as c:
        await c.list_folders()

    req = httpx_mock.get_request()
    assert req.headers["Authorization"] == "my-secret"
    assert "Bearer" not in req.headers["Authorization"]


@pytest.mark.asyncio
async def test_list_videos_paginates(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/videos?folder_id=f1&page=1&limit=100",
        json={"videos": [{"id": f"v{i}", "title": f"T{i}", "folder_id": "f1",
                          "description": "", "length": 100, "size": 1000,
                          "tags": [], "thumbnail": "https://t/1.jpg",
                          "status": "converted", "created_at": "2025-01-01T00:00:00Z"}
                          for i in range(100)]},
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/videos?folder_id=f1&page=2&limit=100",
        json={"videos": [{"id": "v100", "title": "T100", "folder_id": "f1",
                          "description": "", "length": 50, "size": 500,
                          "tags": [], "thumbnail": "https://t/100.jpg",
                          "status": "converted", "created_at": "2025-01-01T00:00:00Z"}]},
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/videos?folder_id=f1&page=3&limit=100",
        json={"videos": []},
    )

    async with PandaClient(api_key="k") as c:
        videos = await c.list_videos("f1")

    assert len(videos) == 101
    assert videos[0].id == "v0"
    assert videos[-1].id == "v100"


# ---------------------------------------------------------------------------
# Task 5 — request_download e poll_download
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_download_posts_payload(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://api-v2.pandavideo.com.br/download-async/v1",
        json={"status": "queued"},
    )
    async with PandaClient(api_key="k") as c:
        await c.request_download("v1", quality="original", title="Aula 01")

    req = httpx_mock.get_request()
    import json as _json
    body = _json.loads(req.content)
    assert body == {
        "quality": "original",
        "format": "video",
        "language": "pt-BR",
        "video_title": "Aula 01",
    }


@pytest.mark.asyncio
async def test_poll_download_returns_url_when_ready(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="GET",
        url="https://api-v2.pandavideo.com.br/download-async/v1/video/original/pt-BR",
        status_code=200,
        json={"url": "https://s3-signed.example/v1.mp4"},
    )
    async with PandaClient(api_key="k") as c:
        url = await c.poll_download("v1", quality="original")
    assert url == "https://s3-signed.example/v1.mp4"


@pytest.mark.asyncio
async def test_poll_download_returns_none_when_processing(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="GET",
        url="https://api-v2.pandavideo.com.br/download-async/v1/video/original/pt-BR",
        status_code=400,
        json={"message": "still processing"},
    )
    async with PandaClient(api_key="k") as c:
        url = await c.poll_download("v1", quality="original")
    assert url is None


# ---------------------------------------------------------------------------
# Task 6 — download_file em streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_file_writes_bytes(httpx_mock: HTTPXMock, tmp_path):
    httpx_mock.add_response(
        url="https://s3-signed.example/v1.mp4",
        content=b"fake-mp4-bytes",
        headers={"content-length": "14"},
    )
    dest = tmp_path / "v1.mp4"
    async with PandaClient(api_key="k") as c:
        size = await c.download_file("https://s3-signed.example/v1.mp4", dest)

    assert dest.read_bytes() == b"fake-mp4-bytes"
    assert size == 14
