from unittest import mock

from ddtrace._trace import http_tags
from ddtrace._trace.http_tags import http_block_metadata
from ddtrace._trace.http_tags import set_client_address_tags
from ddtrace._trace.http_tags import set_query_string_tag
from ddtrace._trace.http_tags import set_status_code_tag
from ddtrace._trace.http_tags import set_url_tags_server
from ddtrace._trace.otel.http import tags
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace.ext import http
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.trace import Span
from ddtrace.trace import tracer
from tests.utils import override_global_config


def test_set_method_tag_removes_stale_original_method():
    span = Span("web.request")

    set_method_tag(span, "custom")
    assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) == "custom"

    set_method_tag(span, "GET")

    assert span.get_tag(otel_http.REQUEST_METHOD) == "GET"
    assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) is None


def test_set_query_string_tag_uses_active_semantics():
    with override_global_config(dict(_otel_trace_semantics_enabled=False)):
        with tracer.trace("web.request") as datadog_span:
            set_query_string_tag(datadog_span, "token=secret")

    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with mock.patch.object(tags, "_obfuscated_query", return_value="token=redacted"):
            with tracer.trace("web.request") as otel_span:
                set_query_string_tag(otel_span, "token=secret")

    assert datadog_span.get_tag(http.QUERY_STRING) == "token=secret"
    assert otel_span.get_tag(otel_http.URL_QUERY) == "token=redacted"


def test_standalone_client_address_tags_use_active_semantics():
    with override_global_config(dict(_otel_trace_semantics_enabled=False)):
        with tracer.trace("web.request") as datadog_span:
            set_client_address_tags(datadog_span, "192.0.2.1")

    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with tracer.trace("web.request") as otel_span:
            set_client_address_tags(otel_span, "192.0.2.2")

    assert datadog_span.get_tag(http.CLIENT_IP) == "192.0.2.1"
    # Without a peer address there is no network.client.ip; the client IP may come from a header.
    assert datadog_span.get_tag("network.client.ip") is None
    assert otel_span.get_tag(otel_http.CLIENT_ADDRESS) == "192.0.2.2"
    assert otel_span.get_tag(otel_http.NETWORK_PEER_ADDRESS) is None


def test_client_address_tags_include_the_peer_address():
    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with tracer.trace("web.request") as span:
            set_client_address_tags(span, "203.0.113.10", "10.0.0.5")

    assert span.get_tag(otel_http.CLIENT_ADDRESS) == "203.0.113.10"
    assert span.get_tag(otel_http.NETWORK_PEER_ADDRESS) == "10.0.0.5"


def test_set_url_tags_server_uses_active_semantics():
    integration_config = IntegrationConfig(config, "test", default_http_tag_query_string="false")

    with override_global_config(dict(_otel_trace_semantics_enabled=False)):
        with tracer.trace("web.request") as datadog_span:
            set_url_tags_server(integration_config, datadog_span, "https://example.com/path?secret=true", "secret=true")

    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with tracer.trace("web.request") as otel_span:
            set_url_tags_server(integration_config, otel_span, "https://example.com/path?secret=true", "secret=true")

    assert datadog_span.get_tag(http.URL) == "https://example.com/path"
    assert otel_span.get_tag(otel_http.URL_PATH) == "/path"


def test_http_block_metadata_uses_active_semantics():
    with override_global_config(dict(_otel_trace_semantics_enabled=False)):
        assert http_block_metadata("get", 403, "token=secret", "agent") == {
            http.STATUS_CODE: "403",
            http.METHOD: "get",
            http.QUERY_STRING: "token=secret",
            http.USER_AGENT: "agent",
        }

    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with mock.patch.object(http_tags, "_obfuscated_query", return_value="token=redacted"):
            assert http_block_metadata("get", 403, "token=secret", "agent") == {
                otel_http.RESPONSE_STATUS_CODE: 403,
                otel_http.REQUEST_METHOD: "GET",
                otel_http.REQUEST_METHOD_ORIGINAL: "get",
                otel_http.URL_QUERY: "token=redacted",
                otel_http.USER_AGENT_ORIGINAL: "agent",
            }


def test_set_status_code_tag_uses_active_semantics():
    with override_global_config(dict(_otel_trace_semantics_enabled=False)):
        with tracer.trace("web.request") as datadog_span:
            set_status_code_tag(datadog_span, 204)

    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with tracer.trace("web.request") as otel_span:
            set_status_code_tag(otel_span, 204)

    assert datadog_span.get_tag(http.STATUS_CODE) == "204"
    assert otel_span.get_metric(otel_http.RESPONSE_STATUS_CODE) == 204
