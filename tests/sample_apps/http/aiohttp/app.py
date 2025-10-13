import aiohttp
import asyncio
from ddtrace import patch

patch(aiohttp=True)

def send_request(method: str, url: str):
    asyncio.run(async_send_request(method,url))

async def async_send_request(method: str, url:str):
    async with aiohttp.ClientSession() as session:
        async with session.request(method=method, url=url) as response:
            print(f"{method} {url}: {response.status}")

send_request("get", "https://example.com")