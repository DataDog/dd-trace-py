import asyncio
import threading
from urllib import request

import pytest

from ddtrace import config
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from tests.utils import assert_is_measured
from tests.utils import override_global_config

from .app.web import setup_app


async def test_full_request(test_spans, patched_app, aiohttp_client):
    client = await aiohttp_client(patched_app)
    # it should create a root span when there is a handler hit
    # with the proper tags
    request = await client.request("GET", "/")
    assert 200 == request.status
    await request.text()
    # the trace is created
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    request_span = traces[0][0]
    assert_is_measured(request_span)

    # request
    assert "aiohttp-web" == request_span.service
    assert "aiohttp.request" == request_span.name
    assert "GET /" == request_span.resource


@pytest.mark.parametrize("path", ["/", "/stream/"])
async def test_request_task_callbacks(test_spans, patched_app, aiohttp_client, path):
    request_tasks = []

    async def capture_request_task(app, handler):
        async def handle(request):
            request_tasks.append(request.task)
            return await handler(request)

        return handle

    patched_app.middlewares.append(capture_request_task)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    callback_errors = []
    loop.set_exception_handler(lambda loop, context: callback_errors.append(context))
    try:
        async with await aiohttp_client(patched_app) as client:
            for _ in range(2):
                response = await client.get(path)
                assert response.status == 200
                await response.text()
        assert len(request_tasks) == 2
        # Closing a keep-alive connection may cancel its task. Wait for task completion
        # and its done callbacks before checking for errors, even if it was cancelled.
        await asyncio.wait_for(asyncio.gather(*request_tasks, return_exceptions=True), timeout=5)
        assert not callback_errors
    finally:
        loop.set_exception_handler(previous_handler)

    traces = test_spans.pop_traces()
    assert len(traces) == 2
    for trace in traces:
        assert len(trace) == 1
        assert trace[0].name == "aiohttp.request"
        assert trace[0].resource == f"GET {path}"


async def test_full_request_w_mem_leak_prevention_flag(test_spans, patched_app, aiohttp_client):
    config.aiohttp.disable_stream_timing_for_mem_leak = True
    try:
        client = await aiohttp_client(patched_app)
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = await client.request("GET", "/")
        assert 200 == request.status
        await request.text()
        # the trace is created
        traces = test_spans.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        request_span = traces[0][0]
        assert_is_measured(request_span)

        # request
        assert "aiohttp-web" == request_span.service
        assert "aiohttp.request" == request_span.name
        assert "GET /" == request_span.resource
    except Exception:
        raise
    finally:
        config.aiohttp.disable_stream_timing_for_mem_leak = False


async def test_stream_request(test_spans, patched_app, aiohttp_client):
    async with await aiohttp_client(patched_app) as client:
        response = await client.request("GET", "/stream/")
        await response.text()
    traces = test_spans.pop_traces()
    request_span = traces[0][0]
    assert abs(0.5 - request_span.duration) < 0.05


async def test_multiple_full_request(test_spans, patched_app, aiohttp_client):
    client = await aiohttp_client(patched_app)

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
    traces = test_spans.pop_traces()
    assert 10 == len(traces)
    assert 1 == len(traces[0])


async def test_user_specified_service(test_spans, aiohttp_client):
    """
    When a service name is specified by the user
        The aiohttp integration should use it as the service name
    """
    with override_global_config(dict(service="mysvc")):
        app = setup_app()
        trace_app(app)
        client = await aiohttp_client(app)
        request = await client.request("GET", "/")
        await request.text()
        traces = test_spans.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        request_span = traces[0][0]
        assert request_span.service == "mysvc"


async def test_http_request_header_tracing(test_spans, patched_app, aiohttp_client):
    client = await aiohttp_client(patched_app)

    config.aiohttp.http.trace_headers(["my-header"])
    request = await client.request("GET", "/", headers={"my-header": "my_value"})
    await request.text()

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.request.headers.my-header") == "my_value"
    assert request_span.get_tag("component") == "aiohttp"
    assert request_span.get_tag("span.kind") == "server"


async def test_http_response_header_tracing(test_spans, patched_app, aiohttp_client):
    client = await aiohttp_client(patched_app)

    config.aiohttp.http.trace_headers(["my-response-header"])
    request = await client.request("GET", "/response_headers/")
    await request.text()

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.response.headers.my-response-header") == "my_response_value"
    assert request_span.get_tag("component") == "aiohttp"
    assert request_span.get_tag("span.kind") == "server"
