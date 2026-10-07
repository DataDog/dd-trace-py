from unittest import mock

from ddtrace._trace.otel.http import tags
from ddtrace._trace.otel.http.tags import http_block_metadata
from ddtrace._trace.otel.http.tags import set_client_address_tags
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace._trace.otel.http.tags import set_query_string_tag
from ddtrace._trace.otel.http.tags import set_status_code_tag
from ddtrace._trace.otel.http.tags import set_url_tags_server
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.settings._config import config
from ddtrace.trace import Span


def test_set_method_tag_removes_stale_original_method():
    span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_method_tag(span, "custom")
        assert span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == "custom"

        set_method_tag(span, "GET")

    assert span.get_tag(http.OTEL_REQUEST_METHOD) == "GET"
    assert span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) is None


def test_semantics_dependent_helpers_read_flag_per_call():
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    datadog_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_url_tags_server(integration_config, datadog_span, "https://example.com/path?secret=true", "secret=true")
        set_method_tag(datadog_span, "get")
        set_status_code_tag(datadog_span, 204)

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_url_tags_server(integration_config, otel_span, "https://example.com/path?secret=true", "secret=true")
        set_method_tag(otel_span, "get")
        set_status_code_tag(otel_span, 204)

    assert datadog_span.get_tag(http.URL) == "https://example.com/path"
    assert datadog_span.get_tag(http.METHOD) == "get"
    assert datadog_span.get_tag(http.STATUS_CODE) == "204"
    assert otel_span.get_tag(http.OTEL_URL_PATH) == "/path"
    assert otel_span.get_tag(http.OTEL_REQUEST_METHOD) == "GET"
    assert otel_span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == "get"
    assert otel_span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == 204


def test_set_query_string_tag_uses_active_semantics():
    datadog_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_query_string_tag(datadog_span, "token=secret")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        with mock.patch.object(tags, "_obfuscated_query", return_value="token=redacted"):
            set_query_string_tag(otel_span, "token=secret")

    assert datadog_span.get_tag(http.QUERY_STRING) == "token=secret"
    assert otel_span.get_tag(http.OTEL_URL_QUERY) == "token=redacted"


def test_standalone_client_address_tags_use_active_semantics():
    datadog_span = Span("web.request")
    otel_span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
        set_client_address_tags(datadog_span, "192.0.2.1")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_client_address_tags(otel_span, "192.0.2.2")

    assert datadog_span.get_tag(http.CLIENT_IP) == "192.0.2.1"
    # Without a peer address there is no network.client.ip; the client IP may come from a header.
    assert datadog_span.get_tag("network.client.ip") is None
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
        with mock.patch.object(tags, "_obfuscated_query", return_value="token=redacted"):
            assert http_block_metadata("get", 403, "token=secret", "agent") == {
                http.OTEL_RESPONSE_STATUS_CODE: 403,
                http.OTEL_REQUEST_METHOD: "GET",
                http.OTEL_REQUEST_METHOD_ORIGINAL: "get",
                http.OTEL_URL_QUERY: "token=redacted",
                http.OTEL_USER_AGENT_ORIGINAL: "agent",
            }
