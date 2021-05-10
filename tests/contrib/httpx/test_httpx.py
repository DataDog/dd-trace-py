import contextlib

import httpx
import pytest

from ddtrace import config
from ddtrace.contrib.httpx.patch import patch
from ddtrace.contrib.httpx.patch import unpatch
from ddtrace.settings.http import HttpConfig
from tests.utils import override_config
from tests.utils import override_http_config
from tests.utils import snapshot_context


# host:port of httpbin_local container
HOST = "localhost"
PORT = 8001


def get_url(path):
    # type: (str) -> str
    return "http://{}:{}{}".format(HOST, PORT, path)


@pytest.fixture(autouse=True)
def patch_httpx():
    patch()
    try:
        yield
    finally:
        unpatch()


@contextlib.contextmanager
def _snapshot_context(request):
    token = ".".join(
        [
            request.node.module.__name__,
            request.node.name,
        ]
    )
    with snapshot_context(token=token):
        yield


# GET 200
@pytest.mark.asyncio
async def test_get_200(request):
    url = get_url("/status/200")
    with _snapshot_context(request):
        resp = httpx.get(url)
        assert resp.status_code == 200

    with _snapshot_context(request):
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_500(request):
    url = get_url("/status/500")
    with _snapshot_context(request):
        resp = httpx.get(url)
        assert resp.status_code == 500

    with _snapshot_context(request):
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            assert resp.status_code == 500


@pytest.mark.asyncio
async def test_split_by_domain(request):
    url = get_url("/status/200")

    with override_config("httpx", {"split_by_domain": True}):
        with _snapshot_context(request):
            resp = httpx.get(url)
            assert resp.status_code == 200

        with _snapshot_context(request):
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trace_query_string(request):
    url = get_url("/status/200?some=query&string=args")

    with override_http_config("httpx", {"trace_query_string": True}):
        with _snapshot_context(request):
            resp = httpx.get(url)
            assert resp.status_code == 200

        with _snapshot_context(request):
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_headers(request):
    url = get_url("/response-headers?Some-Response-Header=Response-Value")

    headers = {
        "Some-Request-Header": "Request-Value",
    }

    try:
        config.httpx.http.trace_headers(["Some-Request-Header", "Some-Response-Header"])
        with _snapshot_context(request):
            resp = httpx.get(url, headers=headers)
            assert resp.status_code == 200

        with _snapshot_context(request):
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
                assert resp.status_code == 200
    finally:
        config.httpx.http = HttpConfig()


@pytest.mark.asyncio
async def test_distributed_tracing_headers(request):
    url = get_url("/headers")

    def assert_request_headers(response):
        data = response.json()
        assert "X-Datadog-Trace-Id" in data["headers"]
        assert "X-Datadog-Parent-Id" in data["headers"]
        assert "X-Datadog-Sampling-Priority" in data["headers"]

    resp = httpx.get(url)
    assert_request_headers(resp)

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        assert_request_headers(resp)
