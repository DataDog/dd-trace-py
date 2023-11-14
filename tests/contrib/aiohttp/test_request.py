import asyncio
import threading
from urllib import request

from ddtrace import config
from ddtrace.contrib.aiohttp.middlewares import trace_app
from ddtrace.contrib.aiohttp.patch import patch
from ddtrace.contrib.aiohttp.patch import unpatch
from tests.utils import assert_is_measured
from tests.utils import override_global_config

from .app.web import setup_app


async def test_full_request(patched_app_tracer, aiohttp_client, loop):
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
    assert_is_measured(request_span)

    # request
    assert "aiohttp-web" == request_span.service
    assert "aiohttp.request" == request_span.name
    assert "GET /" == request_span.resource


async def test_stream_request(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    async with await aiohttp_client(app) as client:
        response = await client.request("GET", "/stream/")
        await response.text()
    traces = tracer.pop_traces()
    request_span = traces[0][0]
    assert abs(0.5 - request_span.duration) < 0.05


async def test_multiple_full_request(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    # it should handle multiple requests using the same loop
    def make_requests():
        url = client.make_url("/delayed/")
        response = request.urlopen(str(url)).read().decode("utf-8")
        assert "Done" == response

    # blocking call executed in different threads
    threads = [threading.Thread(target=make_requests) for _ in range(10)]
    for t in threads:
        t.daemon = True
        t.start()

    # we should yield so that this loop can handle
    # threads' requests
    await asyncio.sleep(0.5)
    for t in threads:
        t.join(timeout=0.5)

    # the trace is created
    traces = tracer.pop_traces()
    assert 10 == len(traces)
    assert 1 == len(traces[0])


async def test_user_specified_service(tracer, aiohttp_client, loop):
    """
    When a service name is specified by the user
        The aiohttp integration should use it as the service name
    """
    unpatch()
    with override_global_config(dict(service="mysvc")):
        patch()
        app = setup_app()
        trace_app(app, tracer)
        client = await aiohttp_client(app)
        request = await client.request("GET", "/")
        await request.text()
        traces = tracer.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        request_span = traces[0][0]
        assert request_span.service == "mysvc"


async def test_http_request_header_tracing(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    config.aiohttp.http.trace_headers(["my-header"])
    request = await client.request("GET", "/", headers={"my-header": "my_value"})
    await request.text()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.request.headers.my-header") == "my_value"
    assert request_span.get_tag("component") == "aiohttp"
    assert request_span.get_tag("span.kind") == "server"


async def test_http_response_header_tracing(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    config.aiohttp.http.trace_headers(["my-response-header"])
    request = await client.request("GET", "/response_headers/")
    await request.text()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.response.headers.my-response-header") == "my_response_value"
    assert request_span.get_tag("component") == "aiohttp"
    assert request_span.get_tag("span.kind") == "server"
