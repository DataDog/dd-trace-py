"""OTLP snapshots of HTTP server span attributes written with OTel semantics enabled.

Each test builds server spans in a ddtrace-run subprocess and fills them through
OTelHTTPSpanAttributes, so the snapshot shows the exported names, typed values, status and span
names without depending on a web framework.
"""

import os

import pytest

from tests.integration.utils import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")

_PREAMBLE = """
from ddtrace import config
from ddtrace._trace.otel.http.tags import OTelHTTPSpanAttributes
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.trace import tracer


def server(
    method, url=None, query=None, raw_uri=None, status=None, route=None, user_agent=None, client=None, peer=None
):
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
        span._set_attribute(SPAN_KIND, SpanKind.SERVER)
        attributes = OTelHTTPSpanAttributes(span, config.flask)
        attributes.set_method(method)
        attributes.set_url(url, query=query, raw_uri=raw_uri)
        attributes.set_user_agent(user_agent)
        if client:
            attributes.set_client_addresses(client, peer)
        attributes.set_status_code(status)
        attributes.set_resource(route)
"""


def _run(ddtrace_run_python_code_in_subprocess, body, env=None):
    # The snapshot context adds the OTel semantics and OTLP export settings to the environment.
    run_env = os.environ.copy()
    run_env.update(env or {})
    code = _PREAMBLE + body + "\ntracer.flush()\n"
    _, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=run_env)
    assert status == 0, err


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_server_request(ddtrace_run_python_code_in_subprocess):
    # Route-based name, raw path, obfuscated query, user agent and both client addresses.
    _run(
        ddtrace_run_python_code_in_subprocess,
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
    )


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_server_unmatched_route(ddtrace_run_python_code_in_subprocess):
    # Without a route the span is named after the method only, never the URL path.
    _run(ddtrace_run_python_code_in_subprocess, 'server("GET", url="http://example.com/no/such/path", status=404)')


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_server_unknown_method(ddtrace_run_python_code_in_subprocess):
    # An unlisted method becomes _OTHER, keeps the original method and names the span HTTP.
    _run(
        ddtrace_run_python_code_in_subprocess,
        'server("PROPFIND", url="http://example.com/items/1", status=200, route="/items/{id}")',
    )


@pytest.mark.parametrize("status_code", [302, 404, 500])
@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_server_status(ddtrace_run_python_code_in_subprocess, status_code):
    # Server spans are errors from 500; error.type is the status code.
    _run(
        ddtrace_run_python_code_in_subprocess,
        f'server("GET", url="http://example.com/", status={status_code}, route="/")',
    )


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_custom_server_error_statuses(ddtrace_run_python_code_in_subprocess):
    # A configured range replaces the OTel default: 404 becomes an error and 500 does not.
    _run(
        ddtrace_run_python_code_in_subprocess,
        """
server("GET", url="http://example.com/missing", status=404, route="/missing")
server("GET", url="http://example.com/broken", status=500, route="/broken")
""",
        env={"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "404-412"},
    )


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_server_query_string_tagging_disabled(ddtrace_run_python_code_in_subprocess):
    # url.query is left out whether the query comes with the URL or on its own.
    _run(
        ddtrace_run_python_code_in_subprocess,
        """
server("GET", url="http://example.com/search?q=public", status=200, route="/search")
server("GET", query="q=public", status=200, route="/search")
""",
        env={"DD_HTTP_SERVER_TAG_QUERY_STRING": "false"},
    )


@pytest.mark.snapshot(otel_semantics=True, ignores=["error.stack"])
def test_otel_semantics_server_exception_error_type(ddtrace_run_python_code_in_subprocess):
    # An exception's type wins over the status code in error.type.
    _run(
        ddtrace_run_python_code_in_subprocess,
        """
with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
    span._set_attribute(SPAN_KIND, SpanKind.SERVER)
    attributes = OTelHTTPSpanAttributes(span, config.flask)
    attributes.set_method("GET")
    attributes.set_url("http://example.com/boom")
    try:
        raise ValueError("request failed")
    except ValueError as exc:
        span.set_exc_info(type(exc), exc, exc.__traceback__)
    attributes.set_status_code(503)
    attributes.set_resource("/boom")
""",
    )
