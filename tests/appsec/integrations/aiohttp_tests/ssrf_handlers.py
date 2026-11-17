import asyncio
import contextlib

import aiohttp
from aiohttp import web

from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges


TIMEOUT = aiohttp.ClientTimeout(total=5)
CLIENT_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)
URLS = []


async def _send(request_context):
    with contextlib.suppress(*CLIENT_ERRORS):
        async with request_context as response:
            await response.read()
    return web.Response(text="ok")


async def session_get(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.get(url))


async def session_get_keyword(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.get(url=url))


async def session_post(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.post(url, data=b"payload"))


async def session_put(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.put(url))


async def session_patch(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.patch(url))


async def session_delete(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.delete(url))


async def session_head(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.head(url))


async def session_options(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.options(url))


async def session_request(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.request("GET", url))


async def session_request_keyword(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.request(method="GET", url=url))


async def module_request(request):
    url = await request.text()
    return await _send(aiohttp.request("GET", url, timeout=TIMEOUT))


async def session_ws_connect(request):
    url = await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        with contextlib.suppress(*CLIENT_ERRORS):
            await session.ws_connect(url)
    return web.Response(text="ok")


async def session_ws_connect_with_credentials(request):
    url = "ws://svc:s3cr3t@127.0.0.1:1/" + await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        with contextlib.suppress(*CLIENT_ERRORS):
            await session.ws_connect(url)
    return web.Response(text="ok")


async def session_get_untainted(request):
    await request.text()
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.get("http://127.0.0.1:1/target"))


async def session_get_tainted_fragment(request):
    url = "http://127.0.0.1:1/target#" + await request.text()
    URLS.append((url, [taint_range.start for taint_range in get_tainted_ranges(url)]))
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        return await _send(session.get(url))
