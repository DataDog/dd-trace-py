from unittest import mock

import pytest

from ddtrace._trace import http_semantics
from ddtrace._trace.http_semantics import OTHER_HTTP_METHOD
from ddtrace._trace.http_semantics import http_block_metadata
from ddtrace._trace.http_semantics import normalize_http_method
from ddtrace._trace.http_semantics import set_method_tag
from ddtrace._trace.http_semantics import set_query_string_tag
from ddtrace._trace.http_semantics import set_status_code_tag
from ddtrace._trace.http_semantics import set_url_tags_otel_client
from ddtrace._trace.http_semantics import set_url_tags_otel_server
from ddtrace._trace.http_semantics import set_url_tags_server
from ddtrace._trace.http_semantics import user_agent_tag
from ddtrace._trace.otel_http_naming import INSTRUMENTATION_HTTP_RESOURCE
from ddtrace._trace.otel_http_naming import RESOURCE_SET_BY_USER
from ddtrace._trace.otel_http_naming import otel_http_resource
from ddtrace._trace.otel_http_naming import record_initial_instrumentation_resource
from ddtrace._trace.otel_http_naming import set_instrumentation_resource
from ddtrace._trace.otel_http_naming import set_otel_http_resource
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.settings._config import config
from ddtrace.trace import Span
from tests.utils import override_env


@pytest.mark.parametrize(
    "method, expected",
    [
        ("GET", ("GET", None)),
        ("get", ("GET", "get")),
        ("QUERY", ("QUERY", None)),
        ("PROPFIND", (OTHER_HTTP_METHOD, "PROPFIND")),
    ],
)
def test_normalize_http_method(method, expected):
    assert normalize_http_method(method) == expected


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
        set_instrumentation_resource(span, "legacy resource")
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
    "netloc, scheme, expected",
    [
        ("example.com", "https", ("example.com", 443)),
        ("example.com:8443", "https", ("example.com", 8443)),
        ("user:password@example.com", "http", ("example.com", 80)),
        ("[::1]:8080", "http", ("::1", 8080)),
    ],
)
def test_split_netloc(netloc, scheme, expected):
    assert http_semantics._split_netloc(netloc, scheme) == expected


def test_credentials_redacted_url():
    assert (
        http_semantics._credentials_redacted_url("https://user:password@example.com/path")
        == "https://REDACTED:REDACTED@example.com/path"
    )
    assert (
        http_semantics._credentials_redacted_url("https://example.com/path@value") == "https://example.com/path@value"
    )


def test_set_url_tags_otel_server():
    integration_config = mock.Mock(http_tag_query_string=True, trace_query_string=False)
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


def test_set_url_tags_otel_client_redacts_credentials_and_drops_query():
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    span = Span("http.request")

    with mock.patch.object(http_semantics, "otel_number", side_effect=str):
        set_url_tags_otel_client(
            integration_config,
            span,
            "https://user:password@example.com/search?q=secret",
            "q=secret",
        )

    assert span.get_tag(http.OTEL_URL_FULL) == "https://REDACTED:REDACTED@example.com/search"
    assert span.get_tag(net.SERVER_ADDRESS) == "example.com"
    assert span.get_tag(net.SERVER_PORT) == "443"


def test_semantics_dependent_helpers_read_flag_per_call():
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    legacy_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_url_tags_server(integration_config, legacy_span, "https://example.com/path?secret=true", "secret=true")
        set_method_tag(legacy_span, "get")
        set_status_code_tag(legacy_span, 204)
        assert user_agent_tag() == http.USER_AGENT

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_url_tags_server(integration_config, otel_span, "https://example.com/path?secret=true", "secret=true")
        set_method_tag(otel_span, "get")
        with mock.patch.object(http_semantics, "otel_number", return_value=204):
            set_status_code_tag(otel_span, 204)
        assert user_agent_tag() == http.OTEL_USER_AGENT_ORIGINAL

    assert legacy_span.get_tag(http.URL) == "https://example.com/path"
    assert legacy_span.get_tag(http.METHOD) == "get"
    assert legacy_span.get_tag(http.STATUS_CODE) == "204"
    assert otel_span.get_tag(http.OTEL_URL_PATH) == "/path"
    assert otel_span.get_tag(http.OTEL_REQUEST_METHOD) == "GET"
    assert otel_span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == "get"
    assert otel_span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == 204


def test_set_query_string_tag_uses_active_semantics():
    legacy_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_query_string_tag(legacy_span, "token=secret")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        with mock.patch.object(http_semantics, "_obfuscated_query", return_value="token=redacted"):
            set_query_string_tag(otel_span, "token=secret")

    assert legacy_span.get_tag(http.QUERY_STRING) == "token=secret"
    assert otel_span.get_tag(http.OTEL_URL_QUERY) == "token=redacted"


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
                    http.OTEL_REQUEST_METHOD: "GET",
                    http.OTEL_REQUEST_METHOD_ORIGINAL: "get",
                    http.OTEL_URL_QUERY: "token=redacted",
                    http.OTEL_USER_AGENT_ORIGINAL: "agent",
                }


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
