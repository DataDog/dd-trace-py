"""OTLP snapshots of HTTP server spans with Datadog and OpenTelemetry semantics.

Each test builds server spans in a ddtrace-run subprocess and fills them through set_http_meta, the
helper web framework integrations use, so each case writes one snapshot per semantics without
depending on a web framework.
"""

import os

import pytest

from tests.integration.utils import AGENT_VERSION


pytestmark = [
    pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent"),
    pytest.mark.parametrize("otel_semantics", ["false", "true"]),
]

_PREAMBLE = """
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.trace import tracer


def server(
    method, url=None, query=None, raw_uri=None, status=None, route=None, user_agent=None, client=None, peer=None
):
    headers = {}
    if user_agent:
        headers["user-agent"] = user_agent
    if client:
        headers["x-forwarded-for"] = client
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
        span._set_attribute(SPAN_KIND, SpanKind.SERVER)
        set_http_meta(
            span,
            config.flask,
            method=method,
            url=url,
            query=query,
            raw_uri=raw_uri,
            status_code=status,
            route=route,
            request_headers=headers or None,
            peer_ip=peer,
        )
"""


def _run(ddtrace_run_python_code_in_subprocess, otel_semantics, body, env=None):
    run_env = os.environ.copy()
    run_env.update(
        {
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
            "DD_TRACE_OTEL_SEMANTICS_ENABLED": otel_semantics,
        }
    )
    run_env.update(env or {})
    code = _PREAMBLE + body + "\ntracer.flush()\n"
    _, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=run_env)
    assert status == 0, err


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_server_request(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # Route, raw path, obfuscated query, user agent and both client addresses.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        """
server(
    "GET",
    url="https://example.com:8443/users/42?token=secret&page=2",
    raw_uri="/users/%34%32?token=secret&page=2",
    status=200,
    route="/users/{id}",
    user_agent="test-agent",
    client="203.0.113.10",
    peer="10.0.0.5",
)
""",
        env={"DD_TRACE_CLIENT_IP_ENABLED": "true"},
    )


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_server_unmatched_route(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # With OTel semantics a span without a route is named after the method only, never the URL path.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        'server("GET", url="http://example.com/no/such/path", status=404)',
    )


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_server_unknown_method(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # With OTel semantics an unlisted method becomes _OTHER, keeps the original method and names the span HTTP.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        'server("PROPFIND", url="http://example.com/items/1", status=200, route="/items/{id}")',
    )


@pytest.mark.parametrize("status_code", [302, 404, 500])
@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_server_status(ddtrace_run_python_code_in_subprocess, otel_semantics, status_code):
    # Server spans are errors from 500; with OTel semantics error.type is the status code.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        f'server("GET", url="http://example.com/", status={status_code}, route="/")',
    )


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_custom_server_error_statuses(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # A configured range replaces the default: 404 becomes an error and 500 does not.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        """
server("GET", url="http://example.com/missing", status=404, route="/missing")
server("GET", url="http://example.com/broken", status=500, route="/broken")
""",
        env={"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "404-412"},
    )


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_server_query_string_tagging_disabled(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # The query is left out whether it comes with the URL or on its own.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        """
server("GET", url="http://example.com/search?q=public", status=200, route="/search")
server("GET", query="q=public", status=200, route="/search")
""",
        env={"DD_HTTP_SERVER_TAG_QUERY_STRING": "false"},
    )


@pytest.mark.snapshot(ignores=["meta.tracestate", "meta.error.stack"])
def test_server_exception_error_type(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # With OTel semantics an exception's type wins over the status code in error.type.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        """
with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
    span._set_attribute(SPAN_KIND, SpanKind.SERVER)
    set_http_meta(span, config.flask, method="GET", url="http://example.com/boom")
    try:
        raise ValueError("request failed")
    except ValueError as exc:
        span.set_exc_info(type(exc), exc, exc.__traceback__)
    set_http_meta(span, config.flask, status_code=503, route="/boom")
""",
    )
