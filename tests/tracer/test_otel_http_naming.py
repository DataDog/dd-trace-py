from unittest import mock

import pytest

from ddtrace._trace import http_semantics
from ddtrace._trace.http_semantics import OTHER_HTTP_METHOD
from ddtrace._trace.http_semantics import OTelHTTPSpanAttributes
from ddtrace._trace.http_semantics import http_block_metadata
from ddtrace._trace.http_semantics import normalize_http_method
from ddtrace._trace.http_semantics import set_client_address_tags
from ddtrace._trace.http_semantics import set_method_tag
from ddtrace._trace.http_semantics import set_query_string_tag
from ddtrace._trace.http_semantics import set_status_code_tag
from ddtrace._trace.http_semantics import set_url_tags_otel_client
from ddtrace._trace.http_semantics import set_url_tags_otel_server
from ddtrace._trace.http_semantics import set_url_tags_server
from ddtrace._trace.http_semantics import set_user_agent_tag
from ddtrace._trace.http_semantics import user_agent_tag
from ddtrace._trace.otel_http_naming import INSTRUMENTATION_HTTP_RESOURCE
from ddtrace._trace.otel_http_naming import RESOURCE_SET_BY_USER
from ddtrace._trace.otel_http_naming import otel_http_resource
from ddtrace._trace.otel_http_naming import record_initial_instrumentation_resource
from ddtrace._trace.otel_http_naming import set_instrumentation_resource
from ddtrace._trace.otel_http_naming import set_otel_http_resource
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.settings._config import config
from ddtrace.trace import Span
from tests.utils import override_env


@pytest.mark.parametrize(
    "method, expected",
    [
        ("GET", ("GET", None)),
        ("get", (OTHER_HTTP_METHOD, "get")),
        ("QUERY", ("QUERY", None)),
        ("PROPFIND", (OTHER_HTTP_METHOD, "PROPFIND")),
    ],
)
def test_normalize_http_method(method, expected):
    assert normalize_http_method(method) == expected


@pytest.mark.subprocess(env={"OTEL_INSTRUMENTATION_HTTP_KNOWN_METHODS": "GET,PROPFIND"})
def test_configured_known_http_methods_replace_defaults_and_are_case_sensitive():
    from ddtrace._trace.http_semantics import OTHER_HTTP_METHOD
    from ddtrace._trace.http_semantics import normalize_http_method

    assert normalize_http_method("PROPFIND") == ("PROPFIND", None)
    assert normalize_http_method("propfind") == (OTHER_HTTP_METHOD, "propfind")
    assert normalize_http_method("POST") == (OTHER_HTTP_METHOD, "POST")


@pytest.mark.parametrize(
    "method, target, expected",
    [
        ("GET", None, "GET"),
        ("GET", "/users/{id}", "GET /users/{id}"),
        (OTHER_HTTP_METHOD, None, "HTTP"),
        (OTHER_HTTP_METHOD, "/users/{id}", "HTTP /users/{id}"),
    ],
)
def test_otel_http_resource(method, target, expected):
    assert otel_http_resource(method, target) == expected


def test_set_otel_http_resource_tracks_instrumentation_and_preserves_user_resource():
    span = Span("http.request")
    span.resource = "integration resource"
    span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)

    set_otel_http_resource(span, "GET", target="/users/{id}")
    assert span.resource == "GET /users/{id}"
    assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "GET /users/{id}"

    span.resource = "user resource"
    set_otel_http_resource(span, "GET", target="/accounts/{id}")
    assert span.resource == "user resource"
    assert span._get_ctx_item(RESOURCE_SET_BY_USER) is True


def test_set_otel_http_resource_leaves_websocket_handshake_unchanged():
    span = Span("web.request", resource="websocket /socket")

    set_otel_http_resource(span, OTHER_HTTP_METHOD, original_method="websocket")

    assert span.resource == "websocket /socket"


def test_resource_ownership_helpers():
    instrumentation_span = Span("http.request", resource="http.request")
    record_initial_instrumentation_resource(instrumentation_span, "http.request")
    assert instrumentation_span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "http.request"

    user_span = Span("http.request", resource="user resource")
    record_initial_instrumentation_resource(user_span, "http.request")
    assert user_span._get_ctx_item(RESOURCE_SET_BY_USER) is True


def test_set_instrumentation_resource_reads_semantics_flag_per_call():
    span = Span("http.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_instrumentation_resource(span, "Datadog resource")
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) is None

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_instrumentation_resource(span, "otel resource")
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "otel resource"


def test_otel_number_reads_export_configuration_per_call():
    with override_env(
        {
            "DD_TRACE_OTEL_SEMANTICS_ENABLED": "false",
            "OTEL_TRACES_EXPORTER": "",
            "DD_TRACE_AGENT_PROTOCOL_VERSION": "",
        }
    ):
        assert http_semantics.otel_number(443) == "443"

    with override_env({"DD_TRACE_OTEL_SEMANTICS_ENABLED": "true"}):
        assert http_semantics.otel_number(443) == 443


@pytest.mark.parametrize(
    "netloc, expected",
    [
        ("example.com", ("example.com", None)),
        ("example.com:8443", ("example.com", 8443)),
        ("user:password@example.com", ("example.com", None)),
        ("[::1]:8080", ("::1", 8080)),
    ],
)
def test_split_netloc(netloc, expected):
    assert http_semantics._split_netloc(netloc) == expected


def test_credentials_redacted_url():
    assert (
        http_semantics._credentials_redacted_url("https://user:password@example.com/path")
        == "https://REDACTED:REDACTED@example.com/path"
    )
    assert (
        http_semantics._credentials_redacted_url("https://example.com/path@value") == "https://example.com/path@value"
    )


def test_set_url_tags_otel_server():
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    span = Span("web.request")

    with mock.patch.object(http_semantics, "_obfuscated_query", return_value="token=redacted"):
        with mock.patch.object(http_semantics, "otel_number", side_effect=str):
            set_url_tags_otel_server(
                integration_config,
                span,
                "https://example.com/users/42?token=secret",
                "token=secret",
                raw_uri="/users/%34%32?token=secret",
            )

    assert span.get_tag(http.OTEL_URL_SCHEME) == "https"
    assert span.get_tag(http.OTEL_URL_PATH) == "/users/%34%32"
    assert span.get_tag(http.OTEL_URL_QUERY) == "token=redacted"
    assert span.get_tag(net.SERVER_ADDRESS) == "example.com"
    assert span.get_tag(net.SERVER_PORT) == "443"


def test_set_url_tags_otel_client_redacts_credentials_and_preserves_query():
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    span = Span("http.request")

    with mock.patch.object(http_semantics, "otel_number", side_effect=str):
        set_url_tags_otel_client(
            integration_config,
            span,
            "https://user:password@example.com/search?q=secret",
            "q=secret",
        )

    assert span.get_tag(http.OTEL_URL_FULL) == "https://REDACTED:REDACTED@example.com/search?q=secret"
    assert span.get_tag(net.SERVER_ADDRESS) == "example.com"
    assert span.get_tag(net.SERVER_PORT) == "443"


def test_semantics_dependent_helpers_read_flag_per_call():
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    datadog_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_url_tags_server(integration_config, datadog_span, "https://example.com/path?secret=true", "secret=true")
        set_method_tag(datadog_span, "get")
        set_status_code_tag(datadog_span, 204)
        assert user_agent_tag() == http.USER_AGENT

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_url_tags_server(integration_config, otel_span, "https://example.com/path?secret=true", "secret=true")
        set_method_tag(otel_span, "get")
        with mock.patch.object(http_semantics, "otel_number", return_value=204):
            set_status_code_tag(otel_span, 204)
        assert user_agent_tag() == http.OTEL_USER_AGENT_ORIGINAL

    assert datadog_span.get_tag(http.URL) == "https://example.com/path"
    assert datadog_span.get_tag(http.METHOD) == "get"
    assert datadog_span.get_tag(http.STATUS_CODE) == "204"
    assert otel_span.get_tag(http.OTEL_URL_PATH) == "/path"
    assert otel_span.get_tag(http.OTEL_REQUEST_METHOD) == OTHER_HTTP_METHOD
    assert otel_span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == "get"
    assert otel_span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == 204


def test_set_query_string_tag_uses_active_semantics():
    datadog_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_query_string_tag(datadog_span, "token=secret")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        with mock.patch.object(http_semantics, "_obfuscated_query", return_value="token=redacted"):
            set_query_string_tag(otel_span, "token=secret")

    assert datadog_span.get_tag(http.QUERY_STRING) == "token=secret"
    assert otel_span.get_tag(http.OTEL_URL_QUERY) == "token=redacted"


def test_set_method_tag_removes_stale_original_method():
    span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_method_tag(span, "custom")
        assert span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == "custom"

        set_method_tag(span, "GET")

    assert span.get_tag(http.OTEL_REQUEST_METHOD) == "GET"
    assert span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) is None


def test_standalone_request_identity_tags_use_active_semantics():
    datadog_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_user_agent_tag(datadog_span, "datadog-agent")
        set_client_address_tags(datadog_span, "192.0.2.1")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_user_agent_tag(otel_span, "otel-agent")
        set_client_address_tags(otel_span, "192.0.2.2")

    assert datadog_span.get_tag(http.USER_AGENT) == "datadog-agent"
    assert datadog_span.get_tag(http.CLIENT_IP) == "192.0.2.1"
    assert datadog_span.get_tag("network.client.ip") == "192.0.2.1"
    assert otel_span.get_tag(http.OTEL_USER_AGENT_ORIGINAL) == "otel-agent"
    assert otel_span.get_tag(http.OTEL_CLIENT_ADDRESS) == "192.0.2.2"
    assert otel_span.get_tag(net.NETWORK_PEER_ADDRESS) is None


def test_http_block_metadata_uses_active_semantics():
    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        assert http_block_metadata("get", 403, "token=secret", "agent") == {
            http.STATUS_CODE: "403",
            http.METHOD: "get",
            http.QUERY_STRING: "token=secret",
            http.USER_AGENT: "agent",
        }

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        with mock.patch.object(http_semantics, "otel_number", return_value=403):
            with mock.patch.object(http_semantics, "_obfuscated_query", return_value="token=redacted"):
                assert http_block_metadata("get", 403, "token=secret", "agent") == {
                    http.OTEL_RESPONSE_STATUS_CODE: 403,
                    http.OTEL_REQUEST_METHOD: OTHER_HTTP_METHOD,
                    http.OTEL_REQUEST_METHOD_ORIGINAL: "get",
                    http.OTEL_URL_QUERY: "token=redacted",
                    http.OTEL_USER_AGENT_ORIGINAL: "agent",
                }


@pytest.fixture
def integration_config():
    return mock.Mock(http_tag_query_string=False, trace_query_string=False)


@pytest.fixture
def server_error_statuses():
    original = config._http_server.error_statuses
    try:
        yield config._http_server
    finally:
        config._http_server.error_statuses = original


@pytest.mark.parametrize(
    "span_type, span_kind, expected",
    [
        (SpanTypes.HTTP, None, True),
        (SpanTypes.WEB, None, False),
        (SpanTypes.WEB, SpanKind.CLIENT, True),
        (SpanTypes.HTTP, SpanKind.SERVER, False),
        (SpanTypes.HTTP, SpanKind.PRODUCER, False),
    ],
)
def test_otel_span_attributes_classifies_client_and_server(integration_config, span_type, span_kind, expected):
    span = Span("request", span_type=span_type)
    if span_kind is not None:
        span._set_attribute(SPAN_KIND, span_kind)

    attributes = OTelHTTPSpanAttributes(span, integration_config)

    assert attributes.is_client is expected


@pytest.mark.parametrize(
    "method, normalized, original",
    [
        ("GET", "GET", None),
        ("get", OTHER_HTTP_METHOD, "get"),
        ("PROPFIND", OTHER_HTTP_METHOD, "PROPFIND"),
    ],
)
def test_otel_span_attributes_sets_method(integration_config, method, normalized, original):
    span = Span("request")
    attributes = OTelHTTPSpanAttributes(span, integration_config)

    attributes.set_method(method)

    assert span.get_tag(http.OTEL_REQUEST_METHOD) == normalized
    assert span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == original


def test_otel_span_attributes_dispatches_client_and_server_urls(integration_config):
    client_span = Span("request", span_type=SpanTypes.HTTP)
    server_span = Span("request", span_type=SpanTypes.WEB)

    with mock.patch.object(http_semantics, "otel_number", side_effect=str):
        OTelHTTPSpanAttributes(client_span, integration_config).set_url("https://example.com/users/42?token=secret")
        OTelHTTPSpanAttributes(server_span, integration_config).set_url(
            "https://example.com/users/42?token=secret",
            raw_uri="/users/%34%32?token=secret",
        )

    assert client_span.get_tag(http.OTEL_URL_FULL) == "https://example.com/users/42?<redacted>"
    assert client_span.get_tag(http.OTEL_URL_PATH) is None
    assert server_span.get_tag(http.OTEL_URL_PATH) == "/users/%34%32"
    assert server_span.get_tag(http.OTEL_URL_QUERY) == "<redacted>"
    assert server_span.get_tag(http.OTEL_URL_FULL) is None


def test_otel_span_attributes_sets_query_without_url(integration_config):
    span = Span("request", span_type=SpanTypes.WEB)

    with mock.patch.object(http_semantics, "_obfuscated_query", return_value="q=public"):
        OTelHTTPSpanAttributes(span, integration_config).set_url(None, query="q=public")

    assert span.get_tag(http.OTEL_URL_QUERY) == "q=public"


def test_otel_span_attributes_server_address_precedence(integration_config):
    url_span = Span("request")
    explicit_span = Span("request")
    fallback_span = Span("request")

    with mock.patch.object(http_semantics, "otel_number", side_effect=str):
        OTelHTTPSpanAttributes(url_span, integration_config).set_url(
            "https://url.example/path",
            server_address="explicit.example",
            fallback_server_address="fallback.example",
        )
    OTelHTTPSpanAttributes(explicit_span, integration_config).set_url(
        None,
        server_address="explicit.example",
        fallback_server_address="fallback.example",
    )
    OTelHTTPSpanAttributes(fallback_span, integration_config).set_url(
        "/relative",
        fallback_server_address="fallback.example",
    )

    assert url_span.get_tag(net.SERVER_ADDRESS) == "url.example"
    assert explicit_span.get_tag(net.SERVER_ADDRESS) == "explicit.example"
    assert fallback_span.get_tag(net.SERVER_ADDRESS) == "fallback.example"


def test_otel_span_attributes_malformed_url_does_not_abort_later_metadata(integration_config):
    span = Span("web.request", span_type=SpanTypes.WEB)
    attributes = OTelHTTPSpanAttributes(span, integration_config)
    attributes.set_method("GET")

    attributes.set_url(
        "http://[::1/path",
        server_address="explicit.example",
        fallback_server_address="fallback.example",
    )
    with mock.patch.object(http_semantics, "otel_number", return_value=503):
        attributes.set_status_code(503)
    attributes.set_resource("/users/{id}")

    assert span.get_tag(net.SERVER_ADDRESS) == "explicit.example"
    assert span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == 503
    assert span.resource == "GET /users/{id}"


@pytest.mark.parametrize(
    "span_type, status_code, expected_error",
    [
        (SpanTypes.HTTP, 399, 0),
        (SpanTypes.HTTP, 400, 1),
        (SpanTypes.WEB, 499, 0),
        (SpanTypes.WEB, 500, 1),
        (SpanTypes.WEB, 600, 1),
        (SpanTypes.WEB, 700, 1),
    ],
)
def test_otel_span_attributes_status_error_semantics(
    integration_config,
    server_error_statuses,
    span_type,
    status_code,
    expected_error,
):
    server_error_statuses.error_statuses = "500-599"
    span = Span("request", span_type=span_type)
    attributes = OTelHTTPSpanAttributes(span, integration_config)

    with mock.patch.object(http_semantics, "otel_number", return_value=status_code):
        attributes.set_status_code(str(status_code))

    assert span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == status_code
    assert span.error == expected_error
    assert span.get_tag(ERROR_TYPE) == (str(status_code) if expected_error else None)


@pytest.mark.parametrize(
    "status_code, expected_error",
    [
        (404, 1),
        (412, 1),
        (413, 0),
        (500, 0),
        (700, 0),
    ],
)
def test_otel_span_attributes_honors_custom_server_error_statuses(
    integration_config,
    server_error_statuses,
    status_code,
    expected_error,
):
    server_error_statuses.error_statuses = "404-412"
    span = Span("request", span_type=SpanTypes.WEB)
    attributes = OTelHTTPSpanAttributes(span, integration_config)

    with mock.patch.object(http_semantics, "otel_number", return_value=status_code):
        attributes.set_status_code(status_code)

    assert span.error == expected_error


def test_programmatic_server_error_status_state_restores(server_error_statuses):
    original_statuses = server_error_statuses.error_statuses
    original_configured = server_error_statuses.error_statuses_configured

    server_error_statuses.error_statuses = "404-412"
    assert server_error_statuses.error_statuses_configured is True

    server_error_statuses.error_statuses = original_statuses
    assert server_error_statuses.error_statuses_configured is original_configured


def test_otel_span_attributes_status_preserves_exception_error_type(integration_config, server_error_statuses):
    server_error_statuses.error_statuses = "500-599"
    span = Span("request", span_type=SpanTypes.WEB)
    span._set_attribute(ERROR_TYPE, "ValueError")
    attributes = OTelHTTPSpanAttributes(span, integration_config)

    with mock.patch.object(http_semantics, "otel_number", return_value=503):
        attributes.set_status_code(503)

    assert span.error == 1
    assert span.get_tag(ERROR_TYPE) == "ValueError"


def test_otel_span_attributes_sets_user_agent_and_client_addresses(integration_config):
    span = Span("request")
    attributes = OTelHTTPSpanAttributes(span, integration_config)

    attributes.set_user_agent("test-agent")
    attributes.set_client_addresses("203.0.113.10", "10.0.0.5")

    assert span.get_tag(http.OTEL_USER_AGENT_ORIGINAL) == "test-agent"
    assert span.get_tag(http.OTEL_CLIENT_ADDRESS) == "203.0.113.10"
    assert span.get_tag(net.NETWORK_PEER_ADDRESS) == "10.0.0.5"


def test_otel_span_attributes_refines_server_resource_with_route(integration_config):
    span = Span("web.request", span_type=SpanTypes.WEB)
    attributes = OTelHTTPSpanAttributes(span, integration_config)
    attributes.set_method("GET")

    attributes.set_resource(None)
    assert span.resource == "GET"

    attributes.set_resource("/users/{id}")
    assert span.resource == "GET /users/{id}"


def test_otel_span_attributes_preserves_method_and_route_across_calls(integration_config):
    span = Span("web.request", span_type=SpanTypes.WEB)
    OTelHTTPSpanAttributes(span, integration_config).set_method("PROPFIND")
    span._set_attribute(http.ROUTE, "/users/{id}")

    OTelHTTPSpanAttributes(span, integration_config).set_resource(None)
    assert span.resource == "HTTP /users/{id}"

    OTelHTTPSpanAttributes(span, integration_config).set_method("PROPFIND")
    OTelHTTPSpanAttributes(span, integration_config).set_resource(None)
    assert span.resource == "HTTP /users/{id}"


def test_otel_span_attributes_client_resource_ignores_server_route(integration_config):
    span = Span("http.request", span_type=SpanTypes.HTTP)
    attributes = OTelHTTPSpanAttributes(span, integration_config)
    attributes.set_method("get")

    attributes.set_resource("/users/{id}")

    assert span.resource == "HTTP"


@pytest.mark.subprocess(env={"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "500-599"})
def test_otel_span_attributes_explicit_default_server_status_does_not_expand():
    from unittest import mock

    from ddtrace._trace import http_semantics
    from ddtrace._trace.http_semantics import OTelHTTPSpanAttributes
    from ddtrace.ext import SpanTypes
    from ddtrace.internal.settings._config import config
    from ddtrace.trace import Span

    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    span = Span("web.request", span_type=SpanTypes.WEB)

    assert config._http_server.error_statuses_configured is True
    with mock.patch.object(http_semantics, "otel_number", return_value=600):
        OTelHTTPSpanAttributes(span, integration_config).set_status_code(600)

    assert span.error == 0


@pytest.mark.subprocess(
    env={
        "DD_TRACE_OTEL_SEMANTICS_ENABLED": "true",
        "DD_TRACE_SPAN_ATTRIBUTE_SCHEMA": "v1",
        "DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED": "true",
    },
    err=None,
)
def test_otel_semantics_overrides_conflicting_schema_and_peer_service_settings():
    from ddtrace.internal.schema import SCHEMA_VERSION
    from ddtrace.internal.settings.peer_service import _ps_config

    assert SCHEMA_VERSION == "v0"
    assert _ps_config.set_defaults_enabled is False
