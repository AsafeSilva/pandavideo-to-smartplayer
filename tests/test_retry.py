import pytest
from pytest_httpx import HTTPXMock

from src.panda_client import PandaClient


@pytest.mark.asyncio
async def test_list_folders_retries_on_5xx(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://api-v2.pandavideo.com.br/folders",
        json={"folders": [{"id": "f1", "name": "OK"}]},
    )
    async with PandaClient(api_key="k") as c:
        folders = await c.list_folders()
    assert folders[0].name == "OK"
    assert len(httpx_mock.get_requests()) == 3
