import asyncio
import threading
from urllib import request

import pytest

from ddtrace import config
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from tests.contrib.otel_http_server import OTEL_SERVER_ENV
from tests.contrib.otel_http_server import OTEL_SERVER_ERROR_STATUSES_ENV
from tests.utils import assert_is_measured
from tests.utils import override_global_config

from .app.web import setup_app


async def test_full_request(test_spans, patched_app, aiohttp_client, request_completed):
    client = await aiohttp_client(patched_app)
    # it should create a root span when there is a handler hit
    # with the proper tags
    request = await client.request("GET", "/")
    assert 200 == request.status
    await request.text()
    await request_completed()
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
@pytest.mark.parametrize("finish_on_prepare", [False, True])
async def test_request_task_callbacks(test_spans, patched_app, aiohttp_client, path, finish_on_prepare, monkeypatch):
    monkeypatch.setitem(config.aiohttp, "disable_stream_timing_for_mem_leak", finish_on_prepare)
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


@pytest.mark.parametrize("path", ["/", "/stream/"])
async def test_request_completion_does_not_wait_for_keepalive(test_spans, patched_app, aiohttp_client, path):
    from ddtrace.internal import core

    completed = asyncio.Queue()
    connection_tasks = []

    async def capture_connection_task(app, handler):
        async def handle(request):
            connection_tasks.append(request.task)
            return await handler(request)

        return handle

    def ended(ctx, exc_info):
        completed.put_nowait(ctx)

    patched_app.middlewares.append(capture_connection_task)
    core.on("context.ended.web.request", ended)
    try:
        async with await aiohttp_client(patched_app) as client:
            for _ in range(2):
                response = await client.get(path)
                assert response.status == 200
                await response.read()
                ctx = await asyncio.wait_for(completed.get(), timeout=5)
                assert ctx._event is None
                assert not connection_tasks[-1].done()
                spans = test_spans.pop()
                assert len(spans) == 1
                assert spans[0].finished
                assert spans[0].resource == f"GET {path}"
    finally:
        core.reset_listeners("context.ended.web.request", ended)


@pytest.mark.parametrize("streaming_subclass", [False, True])
async def test_stream_prepare_in_child_task(test_spans, aiohttp_client, request_completed, streaming_subclass):
    from aiohttp import web

    from ddtrace.trace import tracer

    class StreamingResponse(web.StreamResponse):
        pass

    async def handler(request):
        span = tracer.current_span()
        assert span is not None
        response_type = StreamingResponse if streaming_subclass else web.StreamResponse
        response = response_type()
        await asyncio.wait_for(response.prepare(request), timeout=5)
        await asyncio.sleep(0)
        assert not span.finished
        await response.write(b"body after preparation")
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    async with await aiohttp_client(app) as client:
        response = await client.get("/")
        assert await response.text() == "body after preparation"
        await request_completed()
    spans = test_spans.pop()
    assert len(spans) == 1
    assert spans[0].finished
    assert spans[0].error == 0


async def test_full_request_w_mem_leak_prevention_flag(test_spans, patched_app, aiohttp_client, request_completed):
    config.aiohttp.disable_stream_timing_for_mem_leak = True
    try:
        client = await aiohttp_client(patched_app)
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = await client.request("GET", "/")
        assert 200 == request.status
        await request.text()
        await request_completed()
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


async def test_multiple_full_request(test_spans, patched_app, aiohttp_client, request_completed):
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

    # Yield until the server has completed every request.
    for _ in threads:
        await request_completed()
    for t in threads:
        t.join(timeout=0.5)

    # the trace is created
    traces = test_spans.pop_traces()
    assert 10 == len(traces)
    assert 1 == len(traces[0])


async def test_user_specified_service(test_spans, aiohttp_client, request_completed):
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
        await request_completed()
        traces = test_spans.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        request_span = traces[0][0]
        assert request_span.service == "mysvc"


async def test_http_request_header_tracing(test_spans, patched_app, aiohttp_client, request_completed):
    client = await aiohttp_client(patched_app)

    config.aiohttp.http.trace_headers(["my-header"])
    request = await client.request("GET", "/", headers={"my-header": "my_value"})
    await request.text()
    await request_completed()

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.request.headers.my-header") == "my_value"
    assert request_span.get_tag("component") == "aiohttp"
    assert request_span.get_tag("span.kind") == "server"


async def test_http_response_header_tracing(test_spans, patched_app, aiohttp_client, request_completed):
    client = await aiohttp_client(patched_app)

    config.aiohttp.http.trace_headers(["my-response-header"])
    request = await client.request("GET", "/response_headers/")
    await request.text()
    await request_completed()

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.response.headers.my-response-header") == "my_response_value"
    assert request_span.get_tag("component") == "aiohttp"
    assert request_span.get_tag("span.kind") == "server"


@pytest.mark.subprocess(env=OTEL_SERVER_ENV, err=None)
def test_otel_semantics_server_span_attributes():
    import asyncio
    from functools import partial

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
    from tests.contrib.otel_http_server import TEST_HEADERS
    from tests.contrib.otel_http_server import assert_otel_server_span
    from tests.utils import TracerSpanContainer
    from tests.utils import scoped_tracer

    assert_span = partial(assert_otel_server_span, peer_address=False)

    async def user(request):
        return web.Response(text="ok")

    async def run():
        app = web.Application()
        app.router.add_get("/users/{user_id}", user)
        trace_app(app)

        with scoped_tracer() as tracer:
            spans = TracerSpanContainer(tracer)
            async with TestClient(TestServer(app)) as client:
                response = await client.get("/users/42?q=1", headers=TEST_HEADERS)
                await response.text()
                assert response.status == 200
                span = next(span for span in spans.get_spans() if span.name == "aiohttp.request")
                assert_span(
                    span,
                    method="GET",
                    status=200,
                    path="/users/42",
                    query="q=1",
                    route="/users/{user_id}",
                    resource="GET /users/{user_id}",
                )

    asyncio.run(run())


@pytest.mark.subprocess(env=OTEL_SERVER_ERROR_STATUSES_ENV, err=None)
def test_otel_semantics_server_error_statuses_override():
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
    from tests.contrib.otel_http_server import TEST_HEADERS
    from tests.utils import TracerSpanContainer
    from tests.utils import scoped_tracer

    async def status(request):
        return web.Response(text="status", status=int(request.match_info["code"]))

    async def run():
        app = web.Application()
        app.router.add_get("/status/{code}", status)
        trace_app(app)

        with scoped_tracer() as tracer:
            spans = TracerSpanContainer(tracer)
            async with TestClient(TestServer(app)) as client:

                async def request_span(path):
                    spans.reset()
                    response = await client.get(path, headers=TEST_HEADERS)
                    await response.text()
                    return next(span for span in spans.get_spans() if span.name == "aiohttp.request")

                span = await request_span("/missing")
                assert span.get_metric("http.response.status_code") == 404
                assert span.error == 1
                # aiohttp raises HTTPNotFound, so error.type is the exception type.
                assert span.get_tag("error.type")

                span = await request_span("/status/500")
                assert span.get_metric("http.response.status_code") == 500
                assert span.error == 0
                assert span.get_tag("error.type") is None

    asyncio.run(run())


@pytest.mark.subprocess(env=OTEL_SERVER_ENV, err=None)
def test_otel_semantics_server_unmatched_route_is_not_an_error():
    import asyncio
    from functools import partial

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
    from tests.contrib.otel_http_server import TEST_HEADERS
    from tests.contrib.otel_http_server import assert_otel_server_span
    from tests.utils import TracerSpanContainer
    from tests.utils import scoped_tracer

    assert_span = partial(assert_otel_server_span, peer_address=False)

    async def run():
        app = web.Application()
        trace_app(app)

        with scoped_tracer() as tracer:
            spans = TracerSpanContainer(tracer)
            async with TestClient(TestServer(app)) as client:
                response = await client.get("/no/such/path/123", headers=TEST_HEADERS)
                await response.text()
                span = next(span for span in spans.get_spans() if span.name == "aiohttp.request")
                assert response.status == 404
                assert_span(span, method="GET", status=404, path="/no/such/path/123", resource="GET")

    asyncio.run(run())
