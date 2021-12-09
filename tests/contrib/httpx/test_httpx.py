import os

import httpx
import pytest

from ddtrace import config
from ddtrace.contrib.httpx.patch import patch
from ddtrace.contrib.httpx.patch import unpatch
from ddtrace.pin import Pin
from ddtrace.settings.http import HttpConfig
from ddtrace.vendor.wrapt import ObjectProxy
from tests.utils import override_config
from tests.utils import override_http_config


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


def test_patching():
    """
    When patching httpx library
        We wrap the correct methods
    When unpatching httpx library
        We unwrap the correct methods
    """
    assert isinstance(httpx.Client.send, ObjectProxy)
    assert isinstance(httpx.AsyncClient.send, ObjectProxy)

    unpatch()
    assert not isinstance(httpx.Client.send, ObjectProxy)
    assert not isinstance(httpx.AsyncClient.send, ObjectProxy)


@pytest.mark.asyncio
async def test_get_200(snapshot_context):
    url = get_url("/status/200")
    with snapshot_context():
        resp = httpx.get(url)
        assert resp.status_code == 200

    with snapshot_context():
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_configure_service_name(snapshot_context):
    """
    When setting ddtrace.config.httpx.service_name directly
        We use the value from ddtrace.config.httpx.service_name
    """
    url = get_url("/status/200")

    with override_config("httpx", {"service_name": "test-httpx-service-name"}):
        with snapshot_context():
            resp = httpx.get(url)
            assert resp.status_code == 200

        with snapshot_context():
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_configure_service_name_pin(tracer, test_spans):
    """
    When setting service name via a Pin
        We use the value from the Pin
    """
    url = get_url("/status/200")

    def assert_spans(test_spans, service):
        test_spans.assert_trace_count(1)
        test_spans.assert_span_count(1)
        assert test_spans.spans[0].service == service
        test_spans.reset()

    # override the tracer on the default sync client
    # DEV: `httpx.get` will call `with Client() as client: client.get()`
    Pin.override(httpx.Client, tracer=tracer)

    # sync client
    client = httpx.Client()
    Pin.override(client, service="sync-client", tracer=tracer)

    # async client
    async_client = httpx.AsyncClient()
    Pin.override(async_client, service="async-client", tracer=tracer)

    resp = httpx.get(url)
    assert resp.status_code == 200
    assert_spans(test_spans, service=None)

    resp = client.get(url)
    assert resp.status_code == 200
    assert_spans(test_spans, service="sync-client")

    async with httpx.AsyncClient() as client:
        resp = await async_client.get(url)
        assert resp.status_code == 200
    assert_spans(test_spans, service="async-client")


def test_configure_service_name_env(run_python_code_in_subprocess):
    """
    When setting DD_HTTPX_SERVICE_NAME env variable
        When DD_SERVICE is also set
            We use the value from DD_HTTPX_SERVICE_NAME
    """
    code = """
import asyncio
import sys

import httpx

from ddtrace.contrib.httpx import patch
from tests.contrib.httpx.test_httpx import get_url
from tests.utils import snapshot_context

patch()
url = get_url("/status/200")

async def test():
    token = "tests.contrib.httpx.test_httpx.test_configure_service_name_env"
    with snapshot_context(token=token):
        httpx.get(url)

    with snapshot_context(token=token):
        async with httpx.AsyncClient() as client:
            await client.get(url)

if sys.version_info >= (3, 7, 0):
    asyncio.run(test())
else:
    asyncio.get_event_loop().run_until_complete(test())
    """
    env = os.environ.copy()
    env["DD_HTTPX_SERVICE_NAME"] = "env-overridden-service-name"
    env["DD_SERVICE"] = "global-service-name"
    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


def test_configure_global_service_name_env(run_python_code_in_subprocess):
    """
    When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """
    code = """
import asyncio
import sys

import httpx

from ddtrace.contrib.httpx import patch
from tests.contrib.httpx.test_httpx import get_url
from tests.utils import snapshot_context

patch()
url = get_url("/status/200")

async def test():
    token = "tests.contrib.httpx.test_httpx.test_configure_global_service_name_env"
    with snapshot_context(token=token):
        httpx.get(url)

    with snapshot_context(token=token):
        async with httpx.AsyncClient() as client:
            await client.get(url)

if sys.version_info >= (3, 7, 0):
    asyncio.run(test())
else:
    asyncio.get_event_loop().run_until_complete(test())
    """
    env = os.environ.copy()
    env["DD_SERVICE"] = "global-service-name"
    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.asyncio
async def test_get_500(snapshot_context):
    """
    When the status code is 500
        We mark the span as an error
    """
    url = get_url("/status/500")
    with snapshot_context():
        resp = httpx.get(url)
        assert resp.status_code == 500

    with snapshot_context():
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            assert resp.status_code == 500


@pytest.mark.asyncio
async def test_split_by_domain(snapshot_context):
    """
    When split_by_domain is configure
        We set the service name to the <host>:<port>
    """
    url = get_url("/status/200")

    with override_config("httpx", {"split_by_domain": True}):
        with snapshot_context():
            resp = httpx.get(url)
            assert resp.status_code == 200

        with snapshot_context():
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trace_query_string(snapshot_context):
    """
    When trace_query_string is enabled
        We include the query string as a tag on the span
    """
    url = get_url("/status/200?some=query&string=args")

    with override_http_config("httpx", {"trace_query_string": True}):
        with snapshot_context():
            resp = httpx.get(url)
            assert resp.status_code == 200

        with snapshot_context():
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_headers(snapshot_context):
    """
    When request headers are configured for this integration
        We add the request headers as tags on the span
    """
    url = get_url("/response-headers?Some-Response-Header=Response-Value")

    headers = {
        "Some-Request-Header": "Request-Value",
    }

    try:
        config.httpx.http.trace_headers(["Some-Request-Header", "Some-Response-Header"])
        with snapshot_context():
            resp = httpx.get(url, headers=headers)
            assert resp.status_code == 200

        with snapshot_context():
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
                assert resp.status_code == 200
    finally:
        config.httpx.http = HttpConfig()


@pytest.mark.asyncio
async def test_distributed_tracing_headers():
    """
    By default
        Distributed tracing headers are added to outbound requests
    """
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


@pytest.mark.asyncio
async def test_distributed_tracing_disabled():
    """
    When distributed_tracing is disabled
        We do not add distributed tracing headers to outbound requests
    """
    url = get_url("/headers")

    def assert_request_headers(response):
        data = response.json()
        assert "X-Datadog-Trace-Id" not in data["headers"]
        assert "X-Datadog-Parent-Id" not in data["headers"]
        assert "X-Datadog-Sampling-Priority" not in data["headers"]

    with override_config("httpx", {"distributed_tracing": False}):
        resp = httpx.get(url)
        assert_request_headers(resp)

        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            assert_request_headers(resp)


def test_distributed_tracing_disabled_env(run_python_code_in_subprocess):
    """
    When disabling distributed tracing via env variable
        We do not add distributed tracing headers to outbound requests
    """
    code = """
import asyncio
import sys

import httpx

from ddtrace.contrib.httpx import patch
from tests.contrib.httpx.test_httpx import get_url, override_config

patch()
url = get_url("/headers")

async def test():
    def assert_request_headers(response):
        data = response.json()
        assert "X-Datadog-Trace-Id" not in data["headers"]
        assert "X-Datadog-Parent-Id" not in data["headers"]
        assert "X-Datadog-Sampling-Priority" not in data["headers"]

    resp = httpx.get(url)
    assert_request_headers(resp)

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        assert_request_headers(resp)

if sys.version_info >= (3, 7, 0):
    asyncio.run(test())
else:
    asyncio.get_event_loop().run_until_complete(test())
    """
    env = os.environ.copy()
    env["DD_HTTPX_DISTRIBUTED_TRACING"] = "false"
    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
