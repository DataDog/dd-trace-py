from opentracing.scope_managers.asyncio import AsyncioScopeManager
import pytest

from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.contrib.aiohttp.middlewares import CONFIG_KEY
from ddtrace.contrib.aiohttp.middlewares import trace_app
from ddtrace.contrib.aiohttp.middlewares import trace_middleware
from ddtrace.ext import http
from ddtrace.sampler import RateSampler
from tests.opentracer.utils import init_tracer
from tests.utils import assert_span_http_status_code
from tests.utils import get_root_span

from .app.web import noop_middleware
from .app.web import setup_app


async def test_handler(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    # it should create a root span when there is a handler hit
    # with the proper tags
    request = await client.request("GET", "/")
    assert 200 == request.status
    text = await request.text()
    assert "What's tracing?" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.request" == span.name
    assert "aiohttp-web" == span.service
    assert "web" == span.span_type
    assert "GET /" == span.resource
    assert str(client.make_url("/")) == span.get_tag(http.URL)
    assert "GET" == span.get_tag("http.method")
    assert_span_http_status_code(span, 200)
    assert 0 == span.error


@pytest.mark.parametrize(
    "query_string,trace_query_string",
    (
        ("foo=bar", False),
        ("foo=bar&foo=baz&x=y", False),
        ("foo=bar", True),
        ("foo=bar&foo=baz&x=y", True),
    ),
)
async def test_param_handler(app_tracer, aiohttp_client, loop, query_string, trace_query_string):
    app, tracer = app_tracer
    if trace_query_string:
        app[CONFIG_KEY]["trace_query_string"] = True
    client = await aiohttp_client(app)
    if query_string:
        fqs = "?" + query_string
    else:
        fqs = ""
    # it should manage properly handlers with params
    request = await client.request("GET", "/echo/team" + fqs)
    assert 200 == request.status
    text = await request.text()
    assert "Hello team" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "GET /echo/{name}" == span.resource
    assert str(client.make_url("/echo/team")) == span.get_tag(http.URL)
    assert_span_http_status_code(span, 200)
    if app[CONFIG_KEY].get("trace_query_string"):
        assert query_string == span.get_tag(http.QUERY_STRING)
    else:
        assert http.QUERY_STRING not in span.meta


async def test_404_handler(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    # it should not pollute the resource space
    request = await client.request("GET", "/404/not_found")
    assert 404 == request.status
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "404" == span.resource
    assert str(client.make_url("/404/not_found")) == span.get_tag(http.URL)
    assert "GET" == span.get_tag("http.method")
    assert_span_http_status_code(span, 404)


async def test_server_error(app_tracer, aiohttp_client):
    """
    When a server error occurs (uncaught exception)
        The span should be flagged as an error
    """
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    request = await client.request("GET", "/uncaught_server_error")
    assert request.status == 500
    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]
    assert span.get_tag("http.method") == "GET"
    assert_span_http_status_code(span, 500)
    assert span.error == 1


async def test_500_response_code(app_tracer, aiohttp_client):
    """
    When a 5XX response code is returned
        The span should be flagged as an error
    """
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    request = await client.request("GET", "/caught_server_error")
    assert request.status == 503
    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]
    assert span.get_tag("http.method") == "GET"
    assert_span_http_status_code(span, 503)
    assert span.error == 1


async def test_coroutine_chaining(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    # it should create a trace with multiple spans
    request = await client.request("GET", "/chaining/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 3 == len(traces[0])
    root = traces[0][0]
    handler = traces[0][1]
    coroutine = traces[0][2]
    # root span created in the middleware
    assert "aiohttp.request" == root.name
    assert "GET /chaining/" == root.resource
    assert str(client.make_url("/chaining/")) == root.get_tag(http.URL)
    assert "GET" == root.get_tag("http.method")
    assert_span_http_status_code(root, 200)
    # span created in the coroutine_chaining handler
    assert "aiohttp.coro_1" == handler.name
    assert root.span_id == handler.parent_id
    assert root.trace_id == handler.trace_id
    # span created in the coro_2 handler
    assert "aiohttp.coro_2" == coroutine.name
    assert handler.span_id == coroutine.parent_id
    assert root.trace_id == coroutine.trace_id


async def test_static_handler(app_tracer, aiohttp_client, loop):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    # it should create a trace with multiple spans
    request = await client.request("GET", "/statics/empty.txt")
    assert 200 == request.status
    text = await request.text()
    assert "Static file\n" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # root span created in the middleware
    assert "aiohttp.request" == span.name
    assert "GET /statics" == span.resource
    assert str(client.make_url("/statics/empty.txt")) == span.get_tag(http.URL)
    assert "GET" == span.get_tag("http.method")
    assert_span_http_status_code(span, 200)


async def test_middleware_applied_twice(app_tracer):
    app, tracer = app_tracer
    # it should be idempotent
    app = setup_app(app.loop)
    # the middleware is not present
    assert 1 == len(app.middlewares)
    assert noop_middleware == app.middlewares[0]
    # the middleware is present (with the noop middleware)
    trace_app(app, tracer)
    assert 2 == len(app.middlewares)
    # applying the middleware twice doesn't add it again
    trace_app(app, tracer)
    assert 2 == len(app.middlewares)
    # and the middleware is always the first
    assert trace_middleware == app.middlewares[0]
    assert noop_middleware == app.middlewares[1]


async def test_exception(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    request = await client.request("GET", "/exception")
    assert 500 == request.status
    await request.text()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert 1 == span.error
    assert "GET /exception" == span.resource
    assert "error" == span.get_tag("error.msg")
    assert "Exception: error" in span.get_tag("error.stack")


async def test_async_exception(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    request = await client.request("GET", "/async_exception")
    assert 500 == request.status
    await request.text()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert 1 == span.error
    assert "GET /async_exception" == span.resource
    assert "error" == span.get_tag("error.msg")
    assert "Exception: error" in span.get_tag("error.stack")


async def test_wrapped_coroutine(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    request = await client.request("GET", "/wrapped_coroutine")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 2 == len(spans)
    span = spans[0]
    assert "GET /wrapped_coroutine" == span.resource
    span = spans[1]
    assert "nested" == span.name
    assert span.duration > 0.25, "span.duration={0}".format(span.duration)


async def test_distributed_tracing(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    # distributed tracing is enabled by default
    tracing_headers = {
        "x-datadog-trace-id": "100",
        "x-datadog-parent-id": "42",
    }

    request = await client.request("GET", "/", headers=tracing_headers)
    assert 200 == request.status
    text = await request.text()
    assert "What's tracing?" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right trace_id and parent_id
    assert span.trace_id == 100
    assert span.parent_id == 42
    assert span.get_metric(SAMPLING_PRIORITY_KEY) is None


async def test_distributed_tracing_with_sampling_true(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    tracer.priority_sampler = RateSampler(0.1)

    tracing_headers = {
        "x-datadog-trace-id": "100",
        "x-datadog-parent-id": "42",
        "x-datadog-sampling-priority": "1",
    }

    request = await client.request("GET", "/", headers=tracing_headers)
    assert 200 == request.status
    text = await request.text()
    assert "What's tracing?" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right trace_id and parent_id
    assert 100 == span.trace_id
    assert 42 == span.parent_id
    assert 1 == span.get_metric(SAMPLING_PRIORITY_KEY)


async def test_distributed_tracing_with_sampling_false(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    tracer.priority_sampler = RateSampler(0.9)

    tracing_headers = {
        "x-datadog-trace-id": "100",
        "x-datadog-parent-id": "42",
        "x-datadog-sampling-priority": "0",
    }

    request = await client.request("GET", "/", headers=tracing_headers)
    assert 200 == request.status
    text = await request.text()
    assert "What's tracing?" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right trace_id and parent_id
    assert 100 == span.trace_id
    assert 42 == span.parent_id
    assert 0 == span.get_metric(SAMPLING_PRIORITY_KEY)


async def test_distributed_tracing_disabled(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    # pass headers for distributed tracing
    app["datadog_trace"]["distributed_tracing_enabled"] = False
    tracing_headers = {
        "x-datadog-trace-id": "100",
        "x-datadog-parent-id": "42",
    }

    request = await client.request("GET", "/", headers=tracing_headers)
    assert 200 == request.status
    text = await request.text()
    assert "What's tracing?" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # distributed tracing must be ignored by default
    assert span.trace_id != 100
    assert span.parent_id != 42


async def test_distributed_tracing_sub_span(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    tracer.priority_sampler = RateSampler(1.0)

    # activate distributed tracing
    tracing_headers = {
        "x-datadog-trace-id": "100",
        "x-datadog-parent-id": "42",
        "x-datadog-sampling-priority": "0",
    }

    request = await client.request("GET", "/sub_span", headers=tracing_headers)
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 2 == len(traces[0])
    span, sub_span = traces[0][0], traces[0][1]
    # with the right trace_id and parent_id
    assert 100 == span.trace_id
    assert 42 == span.parent_id
    assert 0 == span.get_metric(SAMPLING_PRIORITY_KEY)
    # check parenting is OK with custom sub-span created within server code
    assert 100 == sub_span.trace_id
    assert span.span_id == sub_span.parent_id
    assert sub_span.get_metric(SAMPLING_PRIORITY_KEY) is None


def _assert_200_parenting(client, traces):
    """Helper to assert parenting when handling aiohttp requests.

    This is used to ensure that parenting is consistent between Datadog
    and OpenTracing implementations of tracing.
    """
    assert 2 == len(traces)
    assert 1 == len(traces[0])

    # the inner span will be the first trace since it completes before the
    # outer span does
    inner_span = traces[0][0]
    outer_span = traces[1][0]

    # confirm the parenting
    assert outer_span.parent_id is None
    assert inner_span.parent_id is None

    assert outer_span.name == "aiohttp_op"

    # with the right fields
    assert "aiohttp.request" == inner_span.name
    assert "aiohttp-web" == inner_span.service
    assert "web" == inner_span.span_type
    assert "GET /" == inner_span.resource
    assert str(client.make_url("/")) == inner_span.get_tag(http.URL)
    assert "GET" == inner_span.get_tag("http.method")
    assert_span_http_status_code(inner_span, 200)
    assert 0 == inner_span.error


async def test_parenting_200_dd(app_tracer, aiohttp_client):
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    with tracer.trace("aiohttp_op"):
        request = await client.request("GET", "/")
        assert 200 == request.status
        text = await request.text()

    assert "What's tracing?" == text
    traces = tracer.pop_traces()
    _assert_200_parenting(client, traces)


async def test_parenting_200_ot(app_tracer, aiohttp_client):
    """OpenTracing version of test_handler."""
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    ot_tracer = init_tracer("aiohttp_svc", tracer, scope_manager=AsyncioScopeManager())

    with ot_tracer.start_active_span("aiohttp_op"):
        request = await client.request("GET", "/")
        assert 200 == request.status
        text = await request.text()

    assert "What's tracing?" == text
    traces = tracer.pop_traces()
    _assert_200_parenting(client, traces)


async def test_analytics_integration_enabled(app_tracer, aiohttp_client):
    """Check trace has analytics sample rate set"""
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    app["datadog_trace"]["analytics_enabled"] = True
    app["datadog_trace"]["analytics_sample_rate"] = 0.5
    request = await client.request("GET", "/template/")
    await request.text()

    # Assert root span sets the appropriate metric
    root = get_root_span(tracer.writer.spans)
    root.assert_structure(dict(name="aiohttp.request", metrics={ANALYTICS_SAMPLE_RATE_KEY: 0.5}))


async def test_analytics_integration_default(app_tracer, aiohttp_client):
    """Check trace has analytics sample rate set"""
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    request = await client.request("GET", "/template/")
    await request.text()

    # Assert root span does not have the appropriate metric
    root = get_root_span(tracer.writer.spans)
    assert root.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None


async def test_analytics_integration_disabled(app_tracer, aiohttp_client):
    """Check trace has analytics sample rate set"""
    app, tracer = app_tracer
    client = await aiohttp_client(app)
    app["datadog_trace"]["analytics_enabled"] = False
    request = await client.request("GET", "/template/")
    await request.text()

    # Assert root span does not have the appropriate metric
    root = get_root_span(tracer.writer.spans)
    assert root.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None
