"""OTLP snapshots of requests client spans with Datadog and OpenTelemetry semantics."""

import pytest


# Settings inherited from the parent environment would change the snapshots.
_ENV = {
    "DD_SERVICE": "otel-http-client-snapshots",
    "DD_ENV": None,
    "DD_VERSION": None,
    "DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING": None,
    "DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": None,
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
}
_SEMANTICS = {"DD_TRACE_OTEL_SEMANTICS_ENABLED": ["false", "true"]}
# The user agent carries the requests version, and httpbin implementations differ in reason phrase case.
_IGNORES = [
    "meta.tracestate",
    "meta.telemetry.sdk.version",
    "meta.http.useragent",
    "meta.user_agent.original",
    "meta.http.status_msg",
]


@pytest.mark.snapshot(ignores=_IGNORES)
@pytest.mark.subprocess(ddtrace_run=True, env=_ENV, parametrize=_SEMANTICS)
def test_client_spans():
    import requests

    from tests.contrib.config import HTTPBIN_CONFIG

    authority = f"{HTTPBIN_CONFIG['host']}:{HTTPBIN_CONFIG['port']}"
    # Credentials are dropped from http.url and redacted in url.full, and the sensitive query value is obfuscated.
    requests.get(f"http://user:pass@{authority}/status/200?token=secret&page=2")
    # With OTel semantics an unlisted method becomes _OTHER, keeps the original method and names the span HTTP.
    # /status/405 answers 405 to this method on every httpbin implementation.
    requests.request("PROPFIND", f"http://{authority}/status/405")
    # With OTel semantics client spans are errors from 400 and error.type is the status code.
    requests.get(f"http://{authority}/status/302", allow_redirects=False)
    requests.get(f"http://{authority}/status/404")
    requests.get(f"http://{authority}/status/500")


@pytest.mark.snapshot(ignores=_IGNORES)
@pytest.mark.subprocess(
    ddtrace_run=True, env=dict(_ENV, DD_TRACE_HTTP_CLIENT_ERROR_STATUSES="200"), parametrize=_SEMANTICS
)
def test_client_spans_custom_error_statuses():
    import requests

    from tests.contrib.config import HTTPBIN_CONFIG

    authority = f"{HTTPBIN_CONFIG['host']}:{HTTPBIN_CONFIG['port']}"
    # With OTel semantics the configured range replaces the default: 200 becomes an error and 404 does not.
    requests.get(f"http://{authority}/status/200")
    requests.get(f"http://{authority}/status/404")
