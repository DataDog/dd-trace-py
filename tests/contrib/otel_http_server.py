"""Shared assertions for HTTP server integration tests run with OTel semantics enabled.

The flag is read once at config creation, so tests that use these helpers must be subprocess tests
started with ``OTEL_SERVER_ENV`` (mocking ``config._otel_trace_semantics_enabled`` skips the derived
settings and records ints as strings). Client addresses are only collected when
DD_TRACE_CLIENT_IP_ENABLED is on, which ``OTEL_SERVER_ENV`` sets.
"""

from typing import Optional


OTEL_SERVER_ENV = {
    "DD_TRACE_OTEL_SEMANTICS_ENABLED": "true",
    "DD_TRACE_CLIENT_IP_ENABLED": "true",
}
# A configured list replaces the default (only 5xx are errors for server spans).
OTEL_SERVER_ERROR_STATUSES_ENV = {**OTEL_SERVER_ENV, "DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "404"}

TEST_USER_AGENT = "otel-server-test/1.0"
TEST_CLIENT_ADDRESS = "203.0.113.7"
TEST_HEADERS = {"User-Agent": TEST_USER_AGENT, "X-Forwarded-For": TEST_CLIENT_ADDRESS}

DD_ONLY_ATTRIBUTES = (
    "http.method",
    "http.url",
    "http.status_code",
    "http.useragent",
    "http.client_ip",
    "network.client.ip",
    "http.hostname",
)


def assert_otel_server_span(
    span,
    *,
    method: str,
    status: int,
    path: str,
    resource: str,
    route: Optional[str] = None,
    original_method: Optional[str] = None,
    query: Optional[str] = None,
    error: Optional[bool] = None,
    client_attributes: bool = True,
    peer_address: bool = True,
) -> None:
    """Assert the OTel HTTP server attributes, their types, and the absence of Datadog-only ones.

    error defaults to the OTel rule (only 5xx). When it is true, error.type must be the status string.
    client_attributes and peer_address can only be turned off for an integration that is known not to
    report them, next to a test that asserts them with assert_otel_client_attributes.
    """
    assert span.resource == resource, span.resource
    assert span.get_tag("http.request.method") == method
    assert span.get_tag("http.request.method_original") == original_method
    assert span.get_tag("url.path") == path
    assert span.get_tag("url.scheme") == "http"
    assert span.get_tag("url.query") == query
    assert span.get_tag("server.address")
    if client_attributes:
        assert_otel_client_attributes(span, peer_address=peer_address)
    assert span.get_tag("http.route") == route

    # Ints are exported as metrics by the test tracer; a tag means the value was stringified.
    server_port = span.get_metric("server.port")
    assert isinstance(server_port, (int, float)) and server_port == int(server_port) and server_port > 0
    assert span.get_tag("server.port") is None
    assert span.get_metric("http.response.status_code") == status
    assert span.get_tag("http.response.status_code") is None

    for name in DD_ONLY_ATTRIBUTES:
        assert span.get_tag(name) is None, name
        assert span.get_metric(name) is None, name

    if error is None:
        error = status >= 500
    if error:
        assert span.error == 1
        assert span.get_tag("error.type") == str(status), span.get_tag("error.type")
    else:
        assert span.error == 0
        assert span.get_tag("error.type") is None


def assert_otel_client_attributes(span, peer_address: bool = True) -> None:
    assert span.get_tag("user_agent.original") == TEST_USER_AGENT
    assert span.get_tag("client.address") == TEST_CLIENT_ADDRESS
    if peer_address:
        assert span.get_tag("network.peer.address")
