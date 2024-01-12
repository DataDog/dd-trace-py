import os

import aiohttp
import pytest

from ddtrace import Pin
from ddtrace.contrib.aiohttp import patch
from ddtrace.contrib.aiohttp import unpatch
from ddtrace.contrib.aiohttp.patch import extract_netloc_and_query_info_from_url
from tests.utils import override_config
from tests.utils import override_http_config

from ..config import HTTPBIN_CONFIG


HOST = HTTPBIN_CONFIG["host"]
PORT = HTTPBIN_CONFIG["port"]
SOCKET = "{}:{}".format(HOST, PORT)
URL = "http://{}".format(SOCKET)
URL_AUTH = "http://user:pass@{}".format(SOCKET)
URL_200 = "{}/status/200".format(URL)
URL_AUTH_200 = "{}/status/200".format(URL_AUTH)
URL_500 = "{}/status/500".format(URL)


@pytest.mark.parametrize("qs,query_res", [("", None), ("?foo=bar&baz=quux", "foo=bar&baz=quux")])
@pytest.mark.parametrize(
    "url,netloc",
    [
        ("http://www.netloc.com/foo", "www.netloc.com"),
        ("http://user:pass@www.netloc.com/foo", "www.netloc.com"),
        ("http://www.netloc.com:3030/foo", "www.netloc.com"),
        ("http://user:pass@www.netloc.com:3030/foo", "www.netloc.com"),
        ("www.netloc.com/foo", "www.netloc.com"),
        ("user:pass@www.netloc.com/foo", "www.netloc.com"),
        ("www.netloc.com:3030/foo", "www.netloc.com"),
        ("user:pass@www.netloc.com:3030/foo", "www.netloc.com"),
    ],
)
def test_extract_from_urlparse(url, netloc, qs, query_res):
    query_url = url + qs
    host, query = extract_netloc_and_query_info_from_url(query_url)
    if query_res is not None:
        assert query == query_res
    assert host == netloc


@pytest.fixture(autouse=True)
def patch_aiohttp_client():
    patch()
    yield
    unpatch()


@pytest.mark.asyncio
async def test_200_request(snapshot_context):
    with snapshot_context():
        async with aiohttp.ClientSession() as session:
            async with session.get(URL_200) as resp:
                assert resp.status == 200


@pytest.mark.asyncio
async def test_auth_200_request(snapshot_context):
    with snapshot_context():
        async with aiohttp.ClientSession() as session:
            async with session.get(URL_AUTH_200) as resp:
                assert resp.status == 200


@pytest.mark.asyncio
async def test_200_request_post(snapshot_context):
    with snapshot_context():
        async with aiohttp.ClientSession() as session:
            async with session.post(URL_200) as resp:
                assert resp.status == 200


@pytest.mark.asyncio
async def test_500_request(snapshot_context):
    with snapshot_context():
        async with aiohttp.ClientSession() as session:
            async with session.get(URL_500) as resp:
                assert resp.status == 500


@pytest.mark.asyncio
async def test_200_request_distributed_tracing(tracer):
    async with aiohttp.ClientSession() as session:
        async with session.get("%s/headers" % URL) as resp:
            assert resp.status == 200
            headers = await resp.json()
            for h in ("x-datadog-trace-id", "x-datadog-parent-id", "x-datadog-sampling-priority"):
                assert h not in headers


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["pin", "global"])
async def test_distributed_tracing_disabled(ddtrace_run_python_code_in_subprocess, variant):
    code = """
import asyncio
import sys
import aiohttp
from ddtrace import Pin
from tests.contrib.aiohttp.test_aiohttp_client import URL

async def test():
    async with aiohttp.ClientSession() as session:
        async with session.get("%s/headers" % URL) as resp:
            assert resp.status == 200
            headers = await resp.json()

        for h in ("x-datadog-trace-id", "x-datadog-parent-id", "x-datadog-sampling-priority"):
            assert h not in headers

asyncio.run(test())
    """
    env = os.environ.copy()
    if variant == "global":
        env["DD_AIOHTTP_CLIENT_DISTRIBUTED_TRACING"] = "false"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
@pytest.mark.snapshot(async_mode=False)
def test_configure_global_service_name_env(ddtrace_run_python_code_in_subprocess, schema_version):
    """
    default/v0/v1 schemas: When only setting DD_SERVICE
        The value from DD_SERVICE is used for all schemas
    """
    code = """
import asyncio
import sys
import aiohttp
from tests.contrib.aiohttp.test_aiohttp_client import URL_200
async def test():
    async with aiohttp.ClientSession() as session:
        async with session.get(URL_200) as resp:
            pass

asyncio.run(test())
    """
    env = os.environ.copy()
    env["DD_SERVICE"] = "global-service-name"
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
@pytest.mark.snapshot(async_mode=False)
def test_unspecified_service_name_env(ddtrace_run_python_code_in_subprocess, schema_version):
    """
    default (v0 schema): When only setting DD_SERVICE
        The value from DD_SERVICE is used
    """
    code = """
import asyncio
import sys
import aiohttp
from tests.contrib.aiohttp.test_aiohttp_client import URL_200
async def test():
    async with aiohttp.ClientSession() as session:
        async with session.get(URL_200) as resp:
            pass

asyncio.run(test())
    """
    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.snapshot(async_mode=False)
def test_configure_service_name_pin(ddtrace_run_python_code_in_subprocess):
    code = """
import asyncio
import sys
import aiohttp
from ddtrace import Pin
from tests.contrib.aiohttp.test_aiohttp_client import URL_200

async def test():
    async with aiohttp.ClientSession() as session:
        Pin.override(session, service="pin-custom-svc")
        async with session.get(URL_200) as resp:
            pass

asyncio.run(test())
    """
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=os.environ.copy())
    assert status == 0, err
    assert err == b""


@pytest.mark.asyncio
async def test_configure_service_name_split_by_domain(snapshot_context):
    """
    When split_by_domain is configured
        We set the service name to the url host
    """
    with override_config("aiohttp_client", {"split_by_domain": True}):
        with snapshot_context():
            async with aiohttp.ClientSession() as session:
                async with session.get(URL_200) as resp:
                    assert resp.status == 200


@pytest.mark.asyncio
async def test_trace_query_string(snapshot_context):
    """
    When trace_query_string is enabled
        The query string is included as a tag on the span
    """
    with override_http_config("aiohttp_client", {"trace_query_string": True}):
        with snapshot_context():
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "%s/?k1=v1&k2=v2" % URL_200,
                    params={
                        "k3": "v3",
                        "k4": "v4",
                    },
                ) as resp:
                    assert resp.status == 404


@pytest.mark.asyncio
async def test_trace_parenting(snapshot_context):
    pin = Pin.get_from(aiohttp)
    tracer = pin.tracer
    with snapshot_context():
        with tracer.trace("parent"):
            async with aiohttp.ClientSession() as session:
                async with session.get(URL_200) as resp:
                    assert resp.status == 200


@pytest.mark.asyncio
async def test_trace_multiple(snapshot_context):
    with snapshot_context():
        async with aiohttp.ClientSession() as session:
            async with session.get(URL_200) as resp:
                assert resp.status == 200
            async with session.get(URL_200) as resp:
                assert resp.status == 200
            async with session.get(URL_200) as resp:
                assert resp.status == 200
