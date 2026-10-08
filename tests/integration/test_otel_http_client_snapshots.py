"""OTLP snapshots of HTTP client spans with Datadog and OpenTelemetry semantics.

Each test builds client spans in a ddtrace-run subprocess and fills them through set_http_meta, the
helper HTTP client integrations use, so each case writes one snapshot per semantics without
depending on an HTTP library or a server.
"""

import os

import pytest

from tests.integration.utils import AGENT_VERSION


pytestmark = [
    pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent"),
    pytest.mark.parametrize("otel_semantics", ["false", "true"]),
]

_PREAMBLE = """
from urllib import parse

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.trace import tracer


def client(method, url, status=None):
    with tracer.trace("http.request", span_type=SpanTypes.HTTP) as span:
        span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
        parsed = parse.urlparse(url)
        set_http_meta(
            span,
            config.requests,
            method=method,
            url=parse.urlunparse(parsed._replace(query="")),
            target_host=parsed.hostname,
            query=parsed.query,
            status_code=status,
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
def test_client_request(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # Credentials are dropped from http.url and redacted in url.full, and the sensitive query value is obfuscated.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        'client("GET", "https://user:pass@example.com/users/42?token=secret&page=2", status=200)',
    )


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_client_unknown_method(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # With OTel semantics an unlisted method becomes _OTHER, keeps the original method and names the span HTTP.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        'client("PROPFIND", "http://example.com/items/1", status=200)',
    )


@pytest.mark.parametrize("status_code", [302, 404, 500])
@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_client_status(ddtrace_run_python_code_in_subprocess, otel_semantics, status_code):
    # With OTel semantics client spans are errors from 400 and error.type is the status code.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        f'client("GET", "http://example.com/", status={status_code})',
    )


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_custom_client_error_statuses(ddtrace_run_python_code_in_subprocess, otel_semantics):
    # A configured range replaces the default: 200 becomes an error and 404 does not.
    _run(
        ddtrace_run_python_code_in_subprocess,
        otel_semantics,
        """
client("GET", "http://example.com/ok", status=200)
client("GET", "http://example.com/missing", status=404)
""",
        env={"DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "200"},
    )
