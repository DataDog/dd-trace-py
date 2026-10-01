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


@pytest.mark.subprocess(env=OTEL_SERVER_ENV, err=None)
def test_otel_semantics_server_span_attributes():
    import asyncio
    from functools import partial

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
    from ddtrace.contrib.internal.aiohttp.patch import unpatch  # noqa: F401
    from tests.contrib.otel_http_server import TEST_HEADERS
    from tests.contrib.otel_http_server import assert_otel_server_span
    from tests.utils import TracerSpanContainer
    from tests.utils import scoped_tracer

    assert_span = partial(assert_otel_server_span, peer_address=False)

    async def user(request):
        return web.Response(text=request.match_info["user_id"])

    async def status(request):
        return web.Response(text="status", status=int(request.match_info["code"]))

    async def propfind(request):
        return web.Response(text="propfind")

    async def run():
        app = web.Application()
        app.router.add_get("/users/{user_id}", user)
        app.router.add_route("PROPFIND", "/users/{user_id}", propfind)
        app.router.add_get("/status/{code}", status)
        trace_app(app)

        with scoped_tracer() as tracer:
            spans = TracerSpanContainer(tracer)
            async with TestClient(TestServer(app)) as client:

                async def request_span(path, method="GET"):
                    spans.reset()
                    response = await client.request(method, path, headers=TEST_HEADERS)
                    await response.text()
                    return response, next(span for span in spans.get_spans() if span.name == "aiohttp.request")

                response, span = await request_span("/users/42?q=1")
                assert response.status == 200
                assert_span(
                    span,
                    method="GET",
                    status=200,
                    path="/users/42",
                    query="q=1",
                    route="/users/{user_id}",
                    resource="GET /users/{user_id}",
                )

                response, span = await request_span("/users/42", method="PROPFIND")
                assert response.status == 200
                assert_span(
                    span,
                    method="_OTHER",
                    original_method="PROPFIND",
                    status=200,
                    path="/users/42",
                    route="/users/{user_id}",
                    resource="HTTP /users/{user_id}",
                )

                response, span = await request_span("/status/418")
                assert response.status == 418
                assert_span(
                    span,
                    method="GET",
                    status=418,
                    path="/status/418",
                    route="/status/{code}",
                    resource="GET /status/{code}",
                )

                response, span = await request_span("/status/500")
                assert response.status == 500
                assert_span(
                    span,
                    method="GET",
                    status=500,
                    path="/status/500",
                    route="/status/{code}",
                    resource="GET /status/{code}",
                )

    asyncio.run(run())


@pytest.mark.subprocess(env=OTEL_SERVER_ERROR_STATUSES_ENV, err=None)
def test_otel_semantics_server_error_statuses_override():
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
    from ddtrace.contrib.internal.aiohttp.patch import unpatch  # noqa: F401
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


# Product bug: the middleware calls set_traceback() for any exception, including the HTTPNotFound
# aiohttp raises for an unmatched route (ddtrace/contrib/internal/aiohttp/middlewares.py:81-83),
# so a 404 is marked as an error although only 5xx statuses are errors under OTel semantics.
@pytest.mark.subprocess(env=OTEL_SERVER_ENV, err=None)
def test_otel_semantics_server_unmatched_route_is_not_an_error():
    import asyncio
    from functools import partial

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
    from ddtrace.contrib.internal.aiohttp.patch import unpatch  # noqa: F401
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
