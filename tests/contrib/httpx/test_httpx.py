import functools

import httpx
import pytest

from ddtrace.contrib.httpx.patch import patch
from ddtrace.contrib.httpx.patch import unpatch
from tests.utils import override_config
from tests.utils import snapshot
from tests.utils import snapshot_context


# host:port of httpbin_local container
HOST = "localhost"
PORT = 8001


def get_url(path):
    # type: (str) -> str
    return "http://{}:{}{}".format(HOST, PORT, path)


@pytest.fixture()
def patch_httpx():
    patch()
    try:
        yield
    finally:
        unpatch()


# GET 200
@snapshot()
def test_get_200(patch_httpx):
    resp = httpx.get(get_url("/status/200"))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_async_get_200(patch_httpx):
    with snapshot_context(token="tests.contrib.httpx.test_httpx.test_get_200"):
        async with httpx.AsyncClient() as client:
            resp = await client.get(get_url("/status/200"))
            assert resp.status_code == 200


# GET 500
@snapshot()
def test_get_500(patch_httpx):
    resp = httpx.get(get_url("/status/500"))
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_async_get_500(patch_httpx):
    with snapshot_context(token="tests.contrib.httpx.test_httpx.test_get_500"):
        async with httpx.AsyncClient() as client:
            resp = await client.get(get_url("/status/500"))
            assert resp.status_code == 500


@snapshot()
def test_split_by_domain(patch_httpx):
    with override_config("httpx", {"split_by_domain": True}):
        resp = httpx.get(get_url("/status/200"))
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_async_split_by_domain(patch_httpx):
    with snapshot_context(token="tests.contrib.httpx.test_httpx.test_split_by_domain"):
        with override_config("httpx", {"split_by_domain": True}):
            async with httpx.AsyncClient() as client:
                resp = await client.get(get_url("/status/200"))
                assert resp.status_code == 200
