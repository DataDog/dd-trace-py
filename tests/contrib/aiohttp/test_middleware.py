import asyncio
import os

import pytest
import pytest_asyncio

from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.aiohttp.middlewares import CONFIG_KEY
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.contrib.internal.aiohttp.middlewares import trace_middleware
from ddtrace.ext import http
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.rate_sampler import RateSampler
from ddtrace.internal.utils.version import parse_version
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config

from .app.web import noop_middleware
from .app.web import setup_app


PYTEST_ASYNCIO_VERSION = parse_version(pytest_asyncio.__version__)


async def test_handler(app, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    # it should create a root span when there is a handler hit
    # with the proper tags
    request = await client.request("GET", "/")
    assert 200 == request.status
    text = await request.text()
    assert "What's tracing?" == text
    # the trace is created
    await request_completed()
    traces = test_spans.pop_traces()
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
    assert "aiohttp" == span.get_tag("component")
    assert_span_http_status_code(span, 200)
    assert 0 == span.error
    assert span.get_tag("span.kind") == "server"


@pytest.mark.skipif(PYTEST_ASYNCIO_VERSION >= (1, 0), reason="'loop' fixture removed")
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_service_operation_schema(ddtrace_run_python_code_in_subprocess, schema_version):
    """
    vO/None:
        operation: module_name.request
        service name: DD_SERVICE
    v1:
        operation: http.server.request
        service name: DD_SERVICE
    """
    expected_service_name = {
        None: "aiohttp-web",
        "v0": "aiohttp-web",
        "v1": None,  # causes a fallback in the test to DEFAULT_SPAN_SERVICE_NAME
    }[schema_version]
    expected_span_name = {None: "aiohttp.request", "v0": "aiohttp.request", "v1": "http.server.request"}[schema_version]
    code = f"""
import pytest
import asyncio
from tests.conftest import *
from tests.contrib.aiohttp.conftest import *
from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME

def test(app, loop, aiohttp_client, test_spans):
    async def async_test(app, aiohttp_client, test_spans):
        if asyncio.iscoroutine(app):
            app = await app
        client = await aiohttp_client(app)
        request = await client.request("GET", "/")
        assert 200 == request.status
        text = await request.text()
        traces = test_spans.pop_traces()
        # Filter by span kind to get only aiohttp server spans (ignore client spans)
        aiohttp_spans = [s for trace in traces for s in trace if s.get_tag("span.kind") == "server"]
        assert len(aiohttp_spans) == 1
        span = aiohttp_spans[0]
        assert span.service == "{expected_service_name}" or DEFAULT_SPAN_SERVICE_NAME
        assert span.name == "{expected_span_name}"
    asyncio.set_event_loop(asyncio.new_event_loop())
    loop.run_until_complete(async_test(app, aiohttp_client, test_spans))


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b""


@pytest.mark.parametrize(
    "query_string,trace_query_string",
    (
        ("foo=bar", False),
        ("foo=bar&foo=baz&x=y", False),
        ("foo=bar", True),
        ("foo=bar&foo=baz&x=y", True),
        ("json=%7B%20%22sign%22%7D", False),
        ("json=%7B%20%22sign%22%7D", True),
    ),
)
async def test_param_handler(app, test_spans, aiohttp_client, query_string, trace_query_string, request_completed):
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
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "GET /echo/{name}" == span.resource
    assert str(client.make_url("/echo/team" + fqs)) == span.get_tag(http.URL)
    assert "aiohttp" == span.get_tag("component")
    assert span.get_tag("span.kind") == "server"
    assert_span_http_status_code(span, 200)
    if app[CONFIG_KEY].get("trace_query_string"):
        assert query_string == span.get_tag(http.QUERY_STRING)
    else:
        assert http.QUERY_STRING not in span.get_tags()


@pytest.mark.parametrize(
    "query_string,trace_query_string",
    (
        ("foo=bar", False),
        ("foo=bar", True),
    ),
)
async def test_param_handler_otel_semantics(
    app, test_spans, aiohttp_client, query_string, trace_query_string, request_completed
):
    app[CONFIG_KEY]["trace_query_string"] = trace_query_string
    client = await aiohttp_client(app)
    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        request = await client.request("GET", "/echo/team?" + query_string)
        assert 200 == request.status
        await request.text()
        await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    span = traces[0][0]
    assert span.resource == "GET /echo/{name}"
    if trace_query_string:
        assert span.get_tag(otel_http.URL_QUERY) == query_string
    else:
        assert span.get_tag(otel_http.URL_QUERY) is None


async def test_404_handler(app, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    # it should not pollute the resource space
    request = await client.request("GET", "/404/not_found")
    assert 404 == request.status
    # the trace is created
    await request.read()
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "404" == span.resource
    assert str(client.make_url("/404/not_found")) == span.get_tag(http.URL)
    assert "GET" == span.get_tag("http.method")
    assert "aiohttp" == span.get_tag("component")
    assert span.get_tag("span.kind") == "server"
    assert_span_http_status_code(span, 404)


@pytest.mark.parametrize(
    "req_url,status,expected_route",
    [
        ("/echo/foo", 200, "/echo/{name}"),
        ("/", 200, "/"),
        ("/uncaught_server_error", 500, "/uncaught_server_error"),
        ("/caught_server_error", 503, "/caught_server_error"),
        ("/statics/empty.txt", 200, "/statics"),
        ("/statics/absent.txt", 404, "/statics"),
    ],
)
async def test_route_reporting_plain_url(
    req_url, status, expected_route, app, test_spans, aiohttp_client, request_completed
):
    client = await aiohttp_client(app)
    request = await client.request("GET", req_url)
    assert status == request.status
    # the trace is created
    await request.read()
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert span.get_tag("http.route") == expected_route


async def test_server_error(app, test_spans, aiohttp_client, request_completed):
    """
    When a server error occurs (uncaught exception)
        The span should be flagged as an error
    """

    client = await aiohttp_client(app)
    request = await client.request("GET", "/uncaught_server_error")
    assert request.status == 500
    await request.read()
    await request_completed()
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]
    assert span.get_tag("http.method") == "GET"
    assert span.get_tag("component") == "aiohttp"
    assert span.get_tag("span.kind") == "server"
    assert_span_http_status_code(span, 500)
    assert span.error == 1


async def test_500_response_code(app, test_spans, aiohttp_client, request_completed):
    """
    When a 5XX response code is returned
        The span should be flagged as an error
    """

    client = await aiohttp_client(app)
    request = await client.request("GET", "/caught_server_error")
    assert request.status == 503
    await request.read()
    await request_completed()
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]
    assert span.get_tag("http.method") == "GET"
    assert span.get_tag("component") == "aiohttp"
    assert span.get_tag("span.kind") == "server"
    assert_span_http_status_code(span, 503)
    assert span.error == 1


async def test_coroutine_chaining(app, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    # it should create a trace with multiple spans
    request = await client.request("GET", "/chaining/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    await request_completed()
    traces = test_spans.pop_traces()
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
    assert root.get_tag("component") == "aiohttp"
    assert root.get_tag("span.kind") == "server"


async def test_static_handler(app, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    # it should create a trace with multiple spans
    request = await client.request("GET", "/statics/empty.txt")
    assert 200 == request.status
    text = await request.text()
    assert "Static file\n" == text
    # the trace is created
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # root span created in the middleware
    assert "aiohttp.request" == span.name
    assert "GET /statics" == span.resource
    assert str(client.make_url("/statics/empty.txt")) == span.get_tag(http.URL)
    assert "GET" == span.get_tag("http.method")
    assert span.get_tag("component") == "aiohttp"
    assert span.get_tag("span.kind") == "server"
    assert_span_http_status_code(span, 200)


async def test_middleware_applied_twice(app, test_spans):
    # it should be idempotent
    app = setup_app(app.loop)
    # the middleware is not present
    assert 1 == len(app.middlewares)
    assert noop_middleware == app.middlewares[0]
    # the middleware is present (with the noop middleware)
    trace_app(app)
    assert 2 == len(app.middlewares)
    # applying the middleware twice doesn't add it again
    trace_app(app)
    assert 2 == len(app.middlewares)
    # and the middleware is always the first
    assert trace_middleware == app.middlewares[0]
    assert noop_middleware == app.middlewares[1]


async def test_exception(app, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    request = await client.request("GET", "/exception")
    assert 500 == request.status
    await request.text()

    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert 1 == span.error
    assert "GET /exception" == span.resource
    assert "error" == span.get_tag(ERROR_MSG)
    assert "Exception: error" in span.get_tag("error.stack")
    assert span.get_tag("component") == "aiohttp"
    assert span.get_tag("span.kind") == "server"


async def test_async_exception(app, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    request = await client.request("GET", "/async_exception")
    assert 500 == request.status
    await request.text()

    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert 1 == span.error
    assert "GET /async_exception" == span.resource
    assert "error" == span.get_tag(ERROR_MSG)
    assert "Exception: error" in span.get_tag("error.stack")
    assert span.get_tag("component") == "aiohttp"
    assert span.get_tag("span.kind") == "server"


async def test_wrapped_coroutine(app, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    request = await client.request("GET", "/wrapped_coroutine")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text

    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 2 == len(spans)
    span = spans[0]
    assert "GET /wrapped_coroutine" == span.resource
    span = spans[1]
    assert "nested" == span.name
    assert span.duration > 0.25, f"span.duration={span.duration}"


async def test_distributed_tracing(app, test_spans, aiohttp_client, request_completed):
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
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right trace_id and parent_id
    assert span.trace_id == 100
    assert span.parent_id == 42
    assert span.get_metric(_SAMPLING_PRIORITY_KEY) is AUTO_KEEP


async def test_distributed_tracing_with_sampling_true(app, tracer, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    tracer._priority_sampler = RateSampler(0.1)

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
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right trace_id and parent_id
    assert 100 == span.trace_id
    assert 42 == span.parent_id
    assert 1 == span.get_metric(_SAMPLING_PRIORITY_KEY)


async def test_distributed_tracing_with_sampling_false(app, tracer, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    tracer._priority_sampler = RateSampler(0.9)

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
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right trace_id and parent_id
    assert 100 == span.trace_id
    assert 42 == span.parent_id
    assert 0 == span.get_metric(_SAMPLING_PRIORITY_KEY)


async def test_distributed_tracing_disabled(app, test_spans, aiohttp_client, request_completed):
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
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # distributed tracing must be ignored by default
    assert span.trace_id != 100
    assert span.parent_id != 42


async def test_distributed_tracing_sub_span(app, tracer, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    tracer._priority_sampler = RateSampler(1.0)

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
    await request_completed()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 2 == len(traces[0])
    span, sub_span = traces[0][0], traces[0][1]
    # with the right trace_id and parent_id
    assert 100 == span.trace_id
    assert 42 == span.parent_id
    assert 0 == span.get_metric(_SAMPLING_PRIORITY_KEY)
    # check parenting is OK with custom sub-span created within server code
    assert 100 == sub_span.trace_id
    assert span.span_id == sub_span.parent_id
    assert sub_span.get_metric(_SAMPLING_PRIORITY_KEY) is None


def _assert_200_parenting(client, traces):
    """Assert parenting when handling aiohttp requests."""
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
    assert "aiohttp" == inner_span.get_tag("component")
    assert inner_span.get_tag("span.kind") == "server"
    assert_span_http_status_code(inner_span, 200)
    assert 0 == inner_span.error


async def test_parenting_200_dd(app, tracer, test_spans, aiohttp_client, request_completed):
    client = await aiohttp_client(app)
    with tracer.trace("aiohttp_op"):
        request = await client.request("GET", "/")
        assert 200 == request.status
        text = await request.text()

    assert "What's tracing?" == text
    await request_completed()
    traces = test_spans.pop_traces()
    _assert_200_parenting(client, traces)


@pytest.mark.parametrize(
    "test_app",
    [
        {"http_method": "GET", "path": "/", "status_code": 200},
        {"http_method": "GET", "path": "/uncaught_server_error", "status_code": 500},
        {"http_method": "GET", "path": "/caught_server_error", "status_code": 503},
    ],
)
@pytest.mark.parametrize(
    "test_headers",
    [
        {
            "type": "default",
            "headers": {
                "x-dd-proxy": "aws-apigateway",
                "x-dd-proxy-request-time-ms": "1736973768000",
                "x-dd-proxy-path": "/",
                "x-dd-proxy-httpmethod": "GET",
                "x-dd-proxy-domain-name": "local",
                "x-dd-proxy-stage": "stage",
            },
        },
        {
            "type": "distributed",
            "headers": {
                "x-dd-proxy": "aws-apigateway",
                "x-dd-proxy-request-time-ms": "1736973768000",
                "x-dd-proxy-path": "/",
                "x-dd-proxy-httpmethod": "GET",
                "x-dd-proxy-domain-name": "local",
                "x-dd-proxy-stage": "stage",
                "x-datadog-trace-id": "1",
                "x-datadog-parent-id": "2",
                "x-datadog-origin": "rum",
                "x-datadog-sampling-priority": "2",
            },
        },
    ],
)
@pytest.mark.parametrize("inferred_proxy_enabled", [False, True])
async def test_inferred_spans_api_gateway(
    app, test_spans, aiohttp_client, test_app, inferred_proxy_enabled, test_headers, request_completed
):
    """
    When making a request to an aiohttp middleware app,
        the aiohttp.request span properly inherits from the inferred span if the setting has been enabled
    """

    client = await aiohttp_client(app)
    with override_global_config(dict(_inferred_proxy_services_enabled=inferred_proxy_enabled)):
        resp = await client.request(test_app["http_method"], test_app["path"], headers=test_headers["headers"])
        assert resp.status == test_app["status_code"]
        await request_completed()
        traces = test_spans.pop_traces()

        if inferred_proxy_enabled is False:
            web_span = traces[0][0]
            assert web_span._parent is None
            assert web_span.name == "aiohttp.request"
            assert len(traces[0]) == 1

            if test_headers["type"] == "distributed":
                assert web_span.trace_id == 1
        else:
            aws_gateway_span = traces[0][0]
            web_span = traces[0][1]
            assert len(traces[0]) == 2
            assert web_span.name == "aiohttp.request"
            # Assert common behavior including aws gateway metadata and web span metadata
            assert_web_and_inferred_aws_api_gateway_span_data(
                aws_gateway_span,
                web_span,
                web_span_name="aiohttp.request",
                web_span_component="aiohttp",
                web_span_service_name="aiohttp-web",
                web_span_resource=test_app["http_method"] + " " + test_app["path"],
                api_gateway_service_name="local",
                api_gateway_resource="GET /",
                method="GET",
                status_code=str(test_app["status_code"]),
                url="https://local/",
                start=1736973768,
                is_distributed=test_headers["type"] == "distributed",
                distributed_trace_id=1,
                distributed_parent_id=2,
                distributed_sampling_priority=USER_KEEP,
            )


@pytest.mark.parametrize("startup", ["manual", "auto", "ddtrace-run"])
@pytest.mark.parametrize("trace_enabled", [True, False])
@pytest.mark.parametrize("apm_tracing_enabled", [True, False])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("response_kind", ["response", "http_error", "stream", "stream_subclass"])
def test_trace_app_without_patch(
    run_python_code_in_subprocess,
    ddtrace_run_python_code_in_subprocess,
    startup,
    trace_enabled,
    apm_tracing_enabled,
    appsec_enabled,
    response_kind,
):
    code = """
import asyncio
import os
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer

from ddtrace.contrib.aiohttp import trace_app
from ddtrace.contrib.internal.aiohttp.middlewares import REQUEST_CONTEXT_KEY
from ddtrace.internal import core
from ddtrace.trace import tracer


expect_span = os.environ["TEST_EXPECT_SPAN"] == "true"
expect_apm = os.environ["TEST_EXPECT_APM"] == "true"
assert tracer.enabled is expect_apm
response_kind = os.environ["TEST_RESPONSE_KIND"]
expected_status = 500 if response_kind == "http_error" else 200
lifecycle = []
request_spans = []
errors = []


def on_started(ctx):
    lifecycle.append(("started", id(ctx)))
    span = ctx.get_item("_inner_span")
    if expect_span:
        assert span is not None
        request_spans.append(span)
    else:
        assert span is None


def on_ended(ctx, exc_info):
    lifecycle.append(("ended", id(ctx)))
    assert ctx.event.response_status_code == expected_status
    assert ctx.event.resource == "GET /"
    assert exc_info == (None, None, None)


def on_exception(event):
    errors.append(str(event.exc_info[1]))


async def handler(request):
    span = tracer.current_span()
    if expect_span:
        assert span is not None
        assert span.name == "aiohttp.request", span.name
        assert request[REQUEST_CONTEXT_KEY] is span.context
    else:
        assert span is None
        assert request[REQUEST_CONTEXT_KEY] is None
    if response_kind == "http_error":
        raise web.HTTPInternalServerError(text="error")
    if response_kind in ("stream", "stream_subclass"):
        class StreamingResponse(web.StreamResponse):
            pass

        response_type = StreamingResponse if response_kind == "stream_subclass" else web.StreamResponse
        response = response_type()
        await response.prepare(request)
        if expect_span:
            assert not span.finished, "Streaming subclasses must retain their request span until completion"
        await response.write(b"ok")
        await response.write_eof()
        return response
    return web.Response(text="ok")


async def main():
    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    # Observe after setup so the tracing subscriber creates the span first.
    core.on("context.started.web.request", on_started)
    core.on("context.ended.web.request", on_ended)
    core.on("web.request.exception", on_exception)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/")
        assert response.status == expected_status, response.status
        assert await response.text() == ("error" if response_kind == "http_error" else "ok")
    assert len(errors) == int(response_kind == "http_error")
    assert len(lifecycle) == 2, lifecycle
    assert lifecycle[0][0] == "started", lifecycle
    assert lifecycle[1] == ("ended", lifecycle[0][1]), lifecycle
    if expect_span:
        assert len(request_spans) == 1
        span = request_spans[0]
        assert span.finished
        assert span.error == (response_kind == "http_error")
        if response_kind == "http_error":
            assert span.get_tag("error.type").endswith("HTTPInternalServerError")


with patch.object(tracer, "start_span", wraps=tracer.start_span) as start_span:
    asyncio.run(main())
    if not expect_span:
        start_span.assert_not_called()
"""
    env = os.environ.copy()
    env["DD_TRACE_ENABLED"] = str(trace_enabled).lower()
    standalone = appsec_enabled and not apm_tracing_enabled
    env["DD_APM_TRACING_ENABLED"] = str(apm_tracing_enabled).lower()
    env["DD_APPSEC_ENABLED"] = str(appsec_enabled).lower()
    env["DD_IAST_ENABLED"] = "false"
    env["DD_APPSEC_SCA_ENABLED"] = "false"
    env["DD_AI_GUARD_ENABLED"] = "false"
    env["TEST_EXPECT_APM"] = str(trace_enabled and not standalone).lower()
    env["TEST_EXPECT_SPAN"] = str(trace_enabled or standalone).lower()
    env["TEST_RESPONSE_KIND"] = response_kind
    if startup == "auto":
        code = "import ddtrace.auto\n" + code
    if startup == "ddtrace-run":
        out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    else:
        out, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


def test_config_set_before_trace_app_import(ddtrace_run_python_code_in_subprocess):
    code = """
import aiohttp

from ddtrace import config

assert config.aiohttp["disable_stream_timing_for_mem_leak"] is False
config.aiohttp.http.trace_headers(["x-request-id"])
config.aiohttp["disable_stream_timing_for_mem_leak"] = True

from ddtrace.contrib.aiohttp import trace_app

assert config.aiohttp.http.header_is_traced("x-request-id")
assert config.aiohttp["disable_stream_timing_for_mem_leak"] is True
"""
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=os.environ.copy())
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


def test_appsec_blocks_without_tracing(run_python_code_in_subprocess):
    code = """
import ddtrace.auto
import asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from ddtrace.contrib.aiohttp import trace_app
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.trace import tracer

calls = []
contexts = []
async def handler(request):
    calls.append(request.path)
    contexts.append(get_active_asm_context() is not None)
    return web.Response(text="ok")

async def main():
    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/?attack=xtrace")
        assert response.status == 403, response.status
        await response.read()
        response = await client.get("/")
        assert response.status == 200, response.status
        assert await response.text() == "ok"
    assert calls == ["/"]
    assert contexts == [True]
    assert tracer.current_span() is None
    assert get_active_asm_context() is None

asyncio.run(main())
"""
    from tests.appsec import rules

    env = os.environ.copy()
    env.update(
        DD_APPSEC_ENABLED="true",
        DD_APM_TRACING_ENABLED="false",
        DD_TRACE_ENABLED="false",
        DD_APPSEC_RULES=rules.RULES_SRB,
    )
    out, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())


@pytest.mark.parametrize("tracing_enabled", [True, False])
@pytest.mark.parametrize("prepared", [True, False])
@pytest.mark.parametrize("finish_on_prepare", [True, False])
async def test_request_exception_does_not_retain_frames(
    test_spans, monkeypatch, tracing_enabled, prepared, finish_on_prepare
):
    import gc
    from unittest.mock import Mock
    import weakref

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from ddtrace import config
    from ddtrace._trace.subscribers import web_framework
    from ddtrace.contrib.internal.aiohttp import middlewares
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    class RequestPayload:
        pass

    class RequestFailure(Exception):
        pass

    monkeypatch.setattr(tracer, "enabled", tracing_enabled)
    monkeypatch.setattr(web_framework.trace_utils, "standalone_config", Mock(apm_opt_out=False))
    monkeypatch.setitem(config.aiohttp, "disable_stream_timing_for_mem_leak", finish_on_prepare)
    app = web.Application()
    middlewares.trace_app(app)
    request = make_mocked_request("GET", "/", app=app)
    request.match_info.route.resource.get_info.return_value = {"path": "/"}
    request.match_info.route.resource.canonical = "/"
    task = asyncio.get_running_loop().create_future()
    request._task = task
    monkeypatch.setattr(middlewares, "current_task", lambda: task)
    response = web.StreamResponse()
    response._req = request
    contexts = []
    payload_refs = []
    completions = []

    def ended(ctx, exc_info):
        completions.append(ctx)

    core.on("context.ended.web.request", ended)

    async def fail(ctx, request, handler):
        contexts.append(ctx)
        payload = RequestPayload()
        payload_refs.append(weakref.ref(payload))
        if prepared:
            await middlewares.on_prepare(request, response)
        raise RequestFailure("request failed")

    monkeypatch.setattr(middlewares, "_handle_request", fail)
    wrapped = await middlewares.trace_middleware(app, Mock())
    was_gc_enabled = gc.isenabled()
    gc.disable()
    try:
        try:
            await wrapped(request)
        except RequestFailure:
            pass
        else:
            raise AssertionError("The request failure must propagate")
        ctx = contexts[0]
        assert ctx.get_item("aiohttp.request.exc_info") is None
        assert payload_refs[0]() is None, "The context must not retain the failing handler frame"
        span = ctx.get_item("_inner_span")
        if tracing_enabled:
            assert span is not None
            assert span.error == 1
            assert span.get_tag("error.type").endswith("RequestFailure")
            assert "request failed" in span.get_tag("error.stack")
        else:
            assert span is None
        # Completion is independent of exception reporting for either timing mode.
        await middlewares.on_prepare(request, web.Response(status=500))
        task.set_result(None)
        await asyncio.sleep(0)
        assert completions == [ctx]
        assert ctx._event is None
        if span is not None:
            assert span.finished
        middlewares.finish_request_span(request, response)
        assert completions == [ctx]
    finally:
        if was_gc_enabled:
            gc.enable()
        core.reset_listeners("context.ended.web.request", ended)


@pytest.mark.parametrize("tracing_enabled", [False, True])
@pytest.mark.parametrize("finish_on_prepare", [False, True])
async def test_sub_app_completes_all_contexts(
    test_spans, monkeypatch, request_completed, tracing_enabled, finish_on_prepare
):
    from unittest.mock import Mock

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace import config
    from ddtrace.contrib.internal import trace_utils
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    monkeypatch.setattr(tracer, "enabled", tracing_enabled)
    monkeypatch.setattr(trace_utils, "standalone_config", Mock(apm_opt_out=False))
    monkeypatch.setitem(config.aiohttp, "disable_stream_timing_for_mem_leak", finish_on_prepare)
    contexts = []

    def started(ctx):
        contexts.append(ctx)

    async def handler(request):
        return web.Response(text="ok")

    app = web.Application()
    sub_app = web.Application()
    sub_app.router.add_get("/", handler)
    trace_app(sub_app)
    app.add_subapp("/sub", sub_app)
    trace_app(app)
    core.on("context.started.web.request", started)
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/sub/")
            assert await response.text() == "ok"
            completed = [await request_completed(), await request_completed()]
        assert len(contexts) == 2
        assert {id(ctx) for ctx in completed} == {id(ctx) for ctx in contexts}
        assert all(ctx._event is None for ctx in contexts)
        for ctx in contexts:
            span = ctx.get_item("_inner_span")
            if tracing_enabled:
                assert span.finished
                assert_span_http_status_code(span, 200)
            else:
                assert span is None
    finally:
        core.reset_listeners("context.started.web.request", started)


@pytest.mark.parametrize("tracing_enabled", [False, True])
@pytest.mark.parametrize("nested", [False, True])
async def test_cancelled_request_completes_without_response(test_spans, monkeypatch, tracing_enabled, nested):
    import asyncio
    from unittest.mock import Mock

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from ddtrace import config
    from ddtrace.contrib.internal import trace_utils
    from ddtrace.contrib.internal.aiohttp import middlewares
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    monkeypatch.setattr(tracer, "enabled", tracing_enabled)
    monkeypatch.setattr(trace_utils, "standalone_config", Mock(apm_opt_out=False))
    monkeypatch.setitem(config.aiohttp, "disable_stream_timing_for_mem_leak", False)
    app = web.Application()
    middlewares.trace_app(app)
    request = make_mocked_request("GET", "/", app=app)
    request.match_info.route.resource.canonical = "/"
    owner = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(middlewares, "current_task", lambda: owner)
    completed = []

    async def cancel(request):
        raise asyncio.CancelledError

    def ended(ctx, exc_info):
        completed.append(ctx)

    core.on("context.ended.web.request", ended)
    try:
        wrapped = await middlewares.trace_middleware(app, cancel)
        if nested:
            wrapped = await middlewares.trace_middleware(app, wrapped)
        with pytest.raises(asyncio.CancelledError):
            await wrapped(request)
        ctx = request[middlewares.REQUEST_EXECUTION_CONTEXT_KEY]
        assert ctx._event is not None
        assert middlewares.REQUEST_STARTED_RESPONSE_KEY not in request
        owner.cancel()
        await asyncio.sleep(0)
        assert len(completed) == (2 if nested else 1)
        assert completed[-1] is ctx
        for completed_ctx in completed:
            assert completed_ctx._event is None
            span = completed_ctx.get_item("_inner_span")
            if tracing_enabled:
                assert span.finished
            else:
                assert span is None
    finally:
        core.reset_listeners("context.ended.web.request", ended)
