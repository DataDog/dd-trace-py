import os
import sys

import aiohttp
import pytest

from ddtrace import Pin
from ddtrace.contrib.aiohttp import patch
from ddtrace.contrib.aiohttp import unpatch
from tests.utils import override_http_config

from ..config import HTTPBIN_CONFIG


HOST = HTTPBIN_CONFIG["host"]
PORT = HTTPBIN_CONFIG["port"]
SOCKET = "{}:{}".format(HOST, PORT)
URL = "http://{}".format(SOCKET)
URL_200 = "{}/status/200".format(URL)
URL_500 = "{}/status/500".format(URL)


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

if sys.version_info >= (3, 7, 0):
    asyncio.run(test())
else:
    asyncio.get_event_loop().run_until_complete(test())
    """
    env = os.environ.copy()
    if variant == "global":
        env["DD_AIOHTTP_CLIENT_DISTRIBUTED_TRACING"] = "false"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.snapshot(async_mode=False)
def test_configure_global_service_name_env(ddtrace_run_python_code_in_subprocess):
    """
    When only setting DD_SERVICE
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

if sys.version_info >= (3, 7, 0):
    asyncio.run(test())
else:
    asyncio.get_event_loop().run_until_complete(test())
    """
    env = os.environ.copy()
    env["DD_SERVICE"] = "global-service-name"
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

if sys.version_info >= (3, 7, 0):
    asyncio.run(test())
else:
    asyncio.get_event_loop().run_until_complete(test())
    """
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=os.environ.copy())
    assert status == 0, err
    assert err == b""


@pytest.mark.asyncio
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python versions below 3.6 sort dictionaries differently")
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
