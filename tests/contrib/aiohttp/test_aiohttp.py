import aiohttp
import pytest

from ..config import HTTPBIN_CONFIG


HOST = HTTPBIN_CONFIG["host"]
PORT = HTTPBIN_CONFIG["port"]
SOCKET = "{}:{}".format(HOST, PORT)
URL_200 = "http://{}/status/200".format(SOCKET)
URL_500 = "http://{}/status/500".format(SOCKET)


@pytest.mark.asyncio
async def test_200_request(snapshot_context):
    with snapshot_context():
        async with aiohttp.ClientSession() as session:
            async with session.get(URL_200) as resp:
                assert resp.status == 200
