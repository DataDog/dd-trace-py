"""OTLP snapshots of HTTP client span attributes written with OTel semantics enabled.

Each test builds client spans in a ddtrace-run subprocess and fills them through
OTelHTTPSpanAttributes, so the snapshot shows the exported names, typed values, status and span
names without depending on an HTTP library or a server.
"""

import os

import pytest

from tests.integration.utils import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")

_PREAMBLE = """
from ddtrace import config
from ddtrace._trace.http_semantics import OTelHTTPSpanAttributes
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.trace import tracer


def client(method, url, status=None):
    with tracer.trace("http.request", span_type=SpanTypes.HTTP) as span:
        span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
        attributes = OTelHTTPSpanAttributes(span, config.requests)
        attributes.set_method(method)
        attributes.set_url(url)
        attributes.set_status_code(status)
        attributes.set_resource(None)
"""


def _run(ddtrace_run_python_code_in_subprocess, body, env=None):
    # The snapshot context adds the OTel semantics and OTLP export settings to the environment.
    run_env = os.environ.copy()
    run_env.update(env or {})
    code = _PREAMBLE + body + "\ntracer.flush()\n"
    _, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=run_env)
    assert status == 0, err


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_client_request(ddtrace_run_python_code_in_subprocess):
    # url.full has no credentials and the sensitive query value is obfuscated.
    _run(
        ddtrace_run_python_code_in_subprocess,
        'client("GET", "https://user:pass@example.com/users/42?token=secret&page=2", status=200)',
    )


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_client_unknown_method(ddtrace_run_python_code_in_subprocess):
    # An unlisted method becomes _OTHER, keeps the original method and names the span HTTP.
    _run(ddtrace_run_python_code_in_subprocess, 'client("PROPFIND", "http://example.com/items/1", status=200)')


@pytest.mark.parametrize("status_code", [302, 404, 500])
@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_client_status(ddtrace_run_python_code_in_subprocess, status_code):
    # Client spans are errors from 400; error.type is the status code.
    _run(ddtrace_run_python_code_in_subprocess, f'client("GET", "http://example.com/", status={status_code})')


@pytest.mark.snapshot(otel_semantics=True)
def test_otel_semantics_custom_client_error_statuses(ddtrace_run_python_code_in_subprocess):
    # A configured range replaces the OTel default: 200 becomes an error and 404 does not.
    _run(
        ddtrace_run_python_code_in_subprocess,
        """
client("GET", "http://example.com/ok", status=200)
client("GET", "http://example.com/missing", status=404)
""",
        env={"DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "200"},
    )
