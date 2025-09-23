"""
Ensure that if the ``AsyncioTracer`` is not properly configured,
bad traces are produced but the ``Context`` object will not
leak memory.
"""
import asyncio
import threading
from urllib import request

import pytest_asyncio

from ddtrace.internal.utils.version import parse_version
from tests.utils import assert_is_measured


PYTEST_ASYNCIO_VERSION = parse_version(pytest_asyncio.__version__)


if PYTEST_ASYNCIO_VERSION < (1, 0):

    async def test_full_request(patched_app_tracer, aiohttp_client, loop):
        return _test_full_request(patched_app_tracer, aiohttp_client, loop=loop)

    async def test_multiple_full_request(patched_app_tracer, aiohttp_client, loop):
        return _test_multiple_full_request(patched_app_tracer, aiohttp_client, loop=loop)

else:

    async def test_full_request(patched_app_tracer, aiohttp_client):
        return _test_full_request(patched_app_tracer, aiohttp_client)

    async def test_multiple_full_request(patched_app_tracer, aiohttp_client):
        return _test_multiple_full_request(patched_app_tracer, aiohttp_client)


async def _test_full_request(patched_app_tracer, aiohttp_client, loop=None):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)
    # it should create a root span when there is a handler hit
    # with the proper tags
    request = await client.request("GET", "/")
    assert 200 == request.status
    await request.text()
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    request_span = traces[0][0]
    # request
    assert_is_measured(request_span)
    assert "aiohttp-web" == request_span.service
    assert "aiohttp.request" == request_span.name
    assert "GET /" == request_span.resource
    assert request_span.get_tag("span.kind") == "server"


async def _test_multiple_full_request(patched_app_tracer, aiohttp_client, loop=None):
    NUMBER_REQUESTS = 10
    responses = []

    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    # it should produce a wrong trace, but the Context must
    # be finished
    def make_requests():
        url = client.make_url("/delayed/")
        response = request.urlopen(str(url)).read().decode("utf-8")
        responses.append(response)

    # blocking call executed in different threads
    threads = [threading.Thread(target=make_requests) for _ in range(NUMBER_REQUESTS)]
    for t in threads:
        t.start()

    # yield back to the event loop until all requests are processed
    while len(responses) < NUMBER_REQUESTS:
        await asyncio.sleep(0.001)

    for response in responses:
        assert "Done" == response

    for t in threads:
        t.join()

    # the trace is wrong but the spans are finished and written
    spans = tracer.pop()
    assert NUMBER_REQUESTS == len(spans)
