from unittest import mock

import pytest

from ddtrace._trace.otel.http import tags
from ddtrace._trace.otel.http.resource import INSTRUMENTATION_HTTP_RESOURCE
from ddtrace._trace.otel.http.resource import RESOURCE_SET_BY_USER
from ddtrace._trace.otel.http.resource import otel_http_resource
from ddtrace._trace.otel.http.resource import record_initial_instrumentation_resource
from ddtrace._trace.otel.http.resource import set_instrumentation_resource
from ddtrace._trace.otel.http.resource import set_otel_http_resource
from ddtrace._trace.otel.http.tags import OTHER_HTTP_METHOD
from ddtrace._trace.otel.http.tags import OTelHTTPSpanAttributes
from ddtrace._trace.otel.http.tags import normalize_http_method
from ddtrace._trace.otel.http.tags import set_url_tags_otel_client
from ddtrace._trace.otel.http.tags import set_url_tags_otel_server
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings import _core as settings_core
from ddtrace.internal.settings._config import config


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


def test_set_otel_http_resource_tracks_instrumentation_and_preserves_user_resource(tracer):
    with tracer.trace("http.request") as span:
        span.resource = "integration resource"
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)

        set_otel_http_resource(span, "GET", target="/users/{id}")
        assert span.resource == "GET /users/{id}"
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "GET /users/{id}"

        span.resource = "user resource"
        set_otel_http_resource(span, "GET", target="/accounts/{id}")
        assert span.resource == "user resource"
        assert span._get_ctx_item(RESOURCE_SET_BY_USER) is True


def test_set_otel_http_resource_leaves_websocket_handshake_unchanged(tracer):
    with tracer.trace("web.request", resource="websocket /socket") as span:
        set_otel_http_resource(span, OTHER_HTTP_METHOD, original_method="websocket")

    assert span.resource == "websocket /socket"


def test_set_otel_http_resource_preserves_empty_user_resource(tracer):
    with tracer.trace("http.request") as span:
        set_otel_http_resource(span, "GET")

        span.resource = ""
        set_otel_http_resource(span, "GET", target="/users/{id}")

    assert span.resource == ""
    assert span._get_ctx_item(RESOURCE_SET_BY_USER) is True


def test_resource_ownership_helpers(tracer):
    with tracer.trace("http.request", resource="http.request") as instrumentation_span:
        record_initial_instrumentation_resource(instrumentation_span, "http.request")
    assert instrumentation_span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "http.request"

    with tracer.trace("http.request", resource="user resource") as user_span:
        record_initial_instrumentation_resource(user_span, "http.request")
    assert user_span._get_ctx_item(RESOURCE_SET_BY_USER) is True


def test_set_instrumentation_resource_reads_semantics_flag_per_call(tracer):
    with tracer.trace("http.request") as span:
        with mock.patch.object(config, "_otel_trace_semantics_enabled", False):
            set_instrumentation_resource(span, "Datadog resource")
            assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) is None

        with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
            set_instrumentation_resource(span, "otel resource")
            assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "otel resource"


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
    assert tags._split_netloc(netloc) == expected


def test_credentials_redacted_url():
    assert (
        tags._credentials_redacted_url("https://user:password@example.com/path")
        == "https://REDACTED:REDACTED@example.com/path"
    )
    assert tags._credentials_redacted_url("https://example.com/path@value") == "https://example.com/path@value"


def test_set_url_tags_otel_server(tracer):
    integration_config = mock.Mock(http_tag_query_string=True, trace_query_string=False)

    with tracer.trace("web.request") as span:
        with mock.patch.object(tags, "_obfuscated_query", return_value="token=redacted"):
            set_url_tags_otel_server(
                integration_config,
                span,
                "https://example.com/users/42?token=secret",
                "token=secret",
                raw_uri="/users/%34%32?token=secret",
            )

    assert span.get_tag(otel_http.URL_SCHEME) == "https"
    assert span.get_tag(otel_http.URL_PATH) == "/users/%34%32"
    assert span.get_tag(otel_http.URL_QUERY) == "token=redacted"
    assert span.get_tag(net.SERVER_ADDRESS) == "example.com"
    assert span.get_metric(otel_http.SERVER_PORT) == 443


@pytest.mark.parametrize(
    "url, raw_uri",
    [
        ("https://example.com/items;version=2?x=1", None),
        ("https://example.com/items?x=1", "/items;version=2?x=1"),
    ],
)
def test_set_url_tags_otel_server_preserves_path_parameters(tracer, url, raw_uri):
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)

    with tracer.trace("web.request") as span:
        set_url_tags_otel_server(integration_config, span, url, None, raw_uri=raw_uri)

    assert span.get_tag(otel_http.URL_PATH) == "/items;version=2"
    assert span.get_tag(otel_http.URL_QUERY) is None


def test_set_url_tags_otel_server_query_falls_back_to_the_url_unless_tagged_separately(tracer):
    integration_config = mock.Mock(http_tag_query_string=True, trace_query_string=False)
    url = "https://example.com/users?page=2"

    with tracer.trace("web.request") as from_url:
        set_url_tags_otel_server(integration_config, from_url, url, None)
    assert from_url.get_tag(otel_http.URL_QUERY) == "page=2"

    with tracer.trace("web.request") as separate:
        set_url_tags_otel_server(integration_config, separate, url, None, query_tagged_separately=True)
    assert separate.get_tag(otel_http.URL_QUERY) is None

    with tracer.trace("web.request") as explicit:
        set_url_tags_otel_server(integration_config, explicit, url, "page=3", query_tagged_separately=True)
    assert explicit.get_tag(otel_http.URL_QUERY) == "page=3"


def test_set_url_tags_otel_server_keeps_leading_double_slash_in_raw_uri(tracer):
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)

    with tracer.trace("web.request") as span:
        set_url_tags_otel_server(integration_config, span, "https://example.com/bar?x=1", None, raw_uri="//foo/bar?x=1")

    assert span.get_tag(otel_http.URL_PATH) == "//foo/bar"


def test_set_url_tags_otel_client_redacts_credentials_and_drops_query(tracer):
    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)

    with tracer.trace("http.request") as span:
        set_url_tags_otel_client(
            integration_config,
            span,
            "https://user:password@example.com/search?q=secret",
            "q=secret",
        )

    assert span.get_tag(otel_http.URL_FULL) == "https://REDACTED:REDACTED@example.com/search"
    assert span.get_tag(net.SERVER_ADDRESS) == "example.com"
    assert span.get_metric(otel_http.SERVER_PORT) == 443


def test_set_url_tags_otel_client_keeps_separate_query_without_obfuscation(tracer):
    integration_config = mock.Mock(http_tag_query_string=True, trace_query_string=False)

    with tracer.trace("http.request") as span:
        with mock.patch.object(config, "_global_query_string_obfuscation_disabled", True):
            set_url_tags_otel_client(
                integration_config,
                span,
                "https://user:password@example.com/search#results",
                "q=visible",
            )

    assert span.get_tag(otel_http.URL_FULL) == "https://REDACTED:REDACTED@example.com/search?q=visible#results"


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


@pytest.fixture
def client_error_statuses():
    original = config._http_client.error_statuses
    try:
        yield config._http_client
    finally:
        config._http_client.error_statuses = original


@pytest.mark.parametrize(
    "span_type, span_kind, expected",
    [
        # Ray Serve proxy requests are HTTP server spans that carry no span.kind.
        (SpanTypes.HTTP, None, False),
        (SpanTypes.HTTP, SpanKind.CLIENT, True),
        (SpanTypes.WEB, None, False),
        (SpanTypes.WEB, SpanKind.CLIENT, True),
        (SpanTypes.HTTP, SpanKind.SERVER, False),
        (SpanTypes.HTTP, SpanKind.PRODUCER, False),
    ],
)
def test_otel_span_attributes_classifies_client_and_server(tracer, integration_config, span_type, span_kind, expected):
    with tracer.trace("request", span_type=span_type) as span:
        if span_kind is not None:
            span._set_attribute(SPAN_KIND, span_kind)

        attributes = OTelHTTPSpanAttributes(span, integration_config)

    assert attributes.is_client is expected


@pytest.mark.parametrize(
    "method, normalized, original",
    [
        ("GET", "GET", None),
        ("get", "GET", "get"),
        ("PROPFIND", OTHER_HTTP_METHOD, "PROPFIND"),
    ],
)
def test_otel_span_attributes_sets_method(tracer, integration_config, method, normalized, original):
    with tracer.trace("request") as span:
        OTelHTTPSpanAttributes(span, integration_config).set_method(method)

    assert span.get_tag(otel_http.REQUEST_METHOD) == normalized
    assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) == original


def test_otel_span_attributes_set_method_clears_stale_original_method(tracer, integration_config):
    with tracer.trace("request") as span:
        OTelHTTPSpanAttributes(span, integration_config).set_method("get")
        OTelHTTPSpanAttributes(span, integration_config).set_method("GET")

    assert span.get_tag(otel_http.REQUEST_METHOD) == "GET"
    assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) is None


def test_otel_span_attributes_dispatches_client_and_server_urls(tracer, integration_config):
    with tracer.trace("request", span_type=SpanTypes.WEB) as server_span:
        OTelHTTPSpanAttributes(server_span, integration_config).set_url(
            "https://example.com/users/42?token=secret",
            raw_uri="/users/%34%32?token=secret",
        )
        with tracer.trace("request", span_type=SpanTypes.HTTP) as client_span:
            client_span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
            OTelHTTPSpanAttributes(client_span, integration_config).set_url("https://example.com/users/42?token=secret")

    assert client_span.get_tag(otel_http.URL_FULL) == "https://example.com/users/42"
    assert client_span.get_tag(otel_http.URL_PATH) is None
    assert server_span.get_tag(otel_http.URL_PATH) == "/users/%34%32"
    assert server_span.get_tag(otel_http.URL_QUERY) is None
    assert server_span.get_tag(otel_http.URL_FULL) is None


def test_otel_span_attributes_sets_query_without_url(tracer):
    integration_config = mock.Mock(http_tag_query_string=True, trace_query_string=False)

    with tracer.trace("request", span_type=SpanTypes.WEB) as span:
        with mock.patch.object(tags, "_obfuscated_query", return_value="q=public"):
            OTelHTTPSpanAttributes(span, integration_config).set_url(None, query="q=public")

    assert span.get_tag(otel_http.URL_QUERY) == "q=public"


def test_otel_span_attributes_query_without_url_respects_query_string_tagging(tracer, integration_config):
    with tracer.trace("request", span_type=SpanTypes.WEB) as span:
        OTelHTTPSpanAttributes(span, integration_config).set_url(None, query="q=public")

    assert span.get_tag(otel_http.URL_QUERY) is None


@pytest.mark.parametrize(
    "url, server_address, fallback_server_address, expected",
    [
        ("https://url.example/path", "explicit.example", "fallback.example", "url.example"),
        (None, "explicit.example", "fallback.example", "explicit.example"),
        ("/relative", None, "fallback.example", "fallback.example"),
    ],
)
def test_otel_span_attributes_server_address_precedence(
    tracer, integration_config, url, server_address, fallback_server_address, expected
):
    with tracer.trace("request") as span:
        OTelHTTPSpanAttributes(span, integration_config).set_url(
            url,
            server_address=server_address,
            fallback_server_address=fallback_server_address,
        )

    assert span.get_tag(net.SERVER_ADDRESS) == expected


def test_otel_span_attributes_malformed_url_does_not_abort_later_metadata(tracer, integration_config):
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
        attributes = OTelHTTPSpanAttributes(span, integration_config)
        attributes.set_method("GET")

        attributes.set_url(
            "http://[::1/path",
            server_address="explicit.example",
            fallback_server_address="fallback.example",
        )
        attributes.set_status_code(503)
        attributes.set_resource("/users/{id}")

    assert span.get_tag(net.SERVER_ADDRESS) == "explicit.example"
    assert span.get_metric(otel_http.RESPONSE_STATUS_CODE) == 503
    assert span.resource == "GET /users/{id}"


@pytest.mark.parametrize(
    "span_type, span_kind, status_code, expected_error",
    [
        (SpanTypes.HTTP, SpanKind.CLIENT, 399, 0),
        (SpanTypes.HTTP, SpanKind.CLIENT, 400, 1),
        (SpanTypes.HTTP, SpanKind.CLIENT, 600, 1),
        (SpanTypes.WEB, None, 499, 0),
        (SpanTypes.WEB, None, 500, 1),
        (SpanTypes.WEB, None, 600, 1),
        (SpanTypes.WEB, None, 700, 1),
    ],
)
def test_otel_span_attributes_status_error_semantics(
    tracer,
    integration_config,
    server_error_statuses,
    span_type,
    span_kind,
    status_code,
    expected_error,
):
    server_error_statuses.error_statuses = "500-599"
    with tracer.trace("request", span_type=span_type) as span:
        if span_kind is not None:
            span._set_attribute(SPAN_KIND, span_kind)
        OTelHTTPSpanAttributes(span, integration_config).set_status_code(str(status_code))

    assert span.get_metric(otel_http.RESPONSE_STATUS_CODE) == status_code
    assert span.error == expected_error
    assert span.get_tag(ERROR_TYPE) == (str(status_code) if expected_error else None)


@pytest.mark.subprocess(env={"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "404-412"})
def test_otel_span_attributes_honors_custom_server_error_statuses():
    from unittest import mock

    from ddtrace._trace.otel.http.tags import OTelHTTPSpanAttributes
    from ddtrace.ext import SpanTypes
    from ddtrace.trace import Span

    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    for status_code, expected_error in ((404, 1), (412, 1), (413, 0), (500, 0), (700, 0)):
        span = Span("request", span_type=SpanTypes.WEB)
        attributes = OTelHTTPSpanAttributes(span, integration_config)

        attributes.set_status_code(status_code)

        assert span.error == expected_error


@pytest.mark.parametrize("source_name", ("LOCAL_CONFIG", "FLEET_CONFIG"))
def test_otel_span_attributes_honors_stable_server_error_statuses(
    tracer, integration_config, server_error_statuses, source_name
):
    source = getattr(settings_core, source_name)
    with mock.patch.dict(source, {"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "404-412"}):
        server_error_statuses.error_statuses = "404-412"
        for status_code, expected_error in ((404, 1), (500, 0)):
            with tracer.trace("request", span_type=SpanTypes.WEB) as span:
                OTelHTTPSpanAttributes(span, integration_config).set_status_code(status_code)
            assert span.error == expected_error


@pytest.mark.parametrize(
    "status_code, expected_error",
    [
        (200, 1),
        (404, 0),
        (500, 0),
    ],
)
def test_otel_span_attributes_honors_custom_client_error_statuses(
    tracer,
    integration_config,
    client_error_statuses,
    status_code,
    expected_error,
):
    with mock.patch.dict(settings_core.LOCAL_CONFIG, {"DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "200"}):
        client_error_statuses.error_statuses = "200"
        with tracer.trace("request", span_type=SpanTypes.HTTP) as span:
            span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
            OTelHTTPSpanAttributes(span, integration_config).set_status_code(status_code)

    assert span.error == expected_error
    assert span.get_tag(ERROR_TYPE) == (str(status_code) if expected_error else None)


def test_client_error_statuses_do_not_change_server_statuses(client_error_statuses, server_error_statuses):
    client_error_statuses.error_statuses = "200"

    assert client_error_statuses.is_error_code(200) is True
    assert server_error_statuses.is_error_code(200) is False


def test_otel_span_attributes_status_preserves_exception_error_type(tracer, integration_config, server_error_statuses):
    server_error_statuses.error_statuses = "500-599"
    with tracer.trace("request", span_type=SpanTypes.WEB) as span:
        span._set_attribute(ERROR_TYPE, "ValueError")
        OTelHTTPSpanAttributes(span, integration_config).set_status_code(503)

    assert span.error == 1
    assert span.get_tag(ERROR_TYPE) == "ValueError"


def test_otel_span_attributes_success_clears_status_error(tracer, integration_config):
    with tracer.trace("request", span_type=SpanTypes.WEB) as span:
        OTelHTTPSpanAttributes(span, integration_config).set_status_code(503)

        OTelHTTPSpanAttributes(span, integration_config).set_status_code(200)

    assert span.get_metric(otel_http.RESPONSE_STATUS_CODE) == 200
    assert span.error == 0
    assert span.get_tag(ERROR_TYPE) is None


def test_otel_span_attributes_success_preserves_exception(tracer, integration_config):
    with tracer.trace("request", span_type=SpanTypes.WEB) as span:
        OTelHTTPSpanAttributes(span, integration_config).set_status_code(503)
        try:
            raise ValueError("request failed")
        except ValueError as exc:
            span.set_exc_info(type(exc), exc, exc.__traceback__)

        OTelHTTPSpanAttributes(span, integration_config).set_status_code(200)

    assert span.get_metric(otel_http.RESPONSE_STATUS_CODE) == 200
    assert span.error == 1
    assert span.get_tag(ERROR_TYPE) == "builtins.ValueError"


def test_otel_span_attributes_sets_user_agent_and_client_addresses(tracer, integration_config):
    with tracer.trace("request") as span:
        attributes = OTelHTTPSpanAttributes(span, integration_config)

        attributes.set_user_agent("test-agent")
        attributes.set_client_addresses("203.0.113.10", "10.0.0.5")

    assert span.get_tag(otel_http.USER_AGENT_ORIGINAL) == "test-agent"
    assert span.get_tag(otel_http.CLIENT_ADDRESS) == "203.0.113.10"
    assert span.get_tag(otel_http.NETWORK_PEER_ADDRESS) == "10.0.0.5"


def test_otel_span_attributes_refines_server_resource_with_route(tracer, integration_config):
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
        attributes = OTelHTTPSpanAttributes(span, integration_config)
        attributes.set_method("GET")

        attributes.set_resource(None)
        assert span.resource == "GET"

        attributes.set_resource("/users/{id}")
        assert span.resource == "GET /users/{id}"


def test_otel_span_attributes_preserves_method_and_route_across_calls(tracer, integration_config):
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
        OTelHTTPSpanAttributes(span, integration_config).set_method("PROPFIND")
        span._set_attribute(http.ROUTE, "/users/{id}")

        OTelHTTPSpanAttributes(span, integration_config).set_resource(None)
        assert span.resource == "HTTP /users/{id}"

        OTelHTTPSpanAttributes(span, integration_config).set_method("PROPFIND")
        OTelHTTPSpanAttributes(span, integration_config).set_resource(None)
        assert span.resource == "HTTP /users/{id}"


def test_otel_span_attributes_client_resource_ignores_server_route(tracer, integration_config):
    with tracer.trace("http.request", span_type=SpanTypes.HTTP) as span:
        span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
        attributes = OTelHTTPSpanAttributes(span, integration_config)
        attributes.set_method("get")

        attributes.set_resource("/users/{id}")

    assert span.resource == "GET"


@pytest.mark.subprocess(env={"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "500-599"})
def test_otel_span_attributes_explicit_default_server_status_does_not_expand():
    from unittest import mock

    from ddtrace._trace.otel.http.tags import OTelHTTPSpanAttributes
    from ddtrace.ext import SpanTypes
    from ddtrace.trace import Span

    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    span = Span("web.request", span_type=SpanTypes.WEB)

    OTelHTTPSpanAttributes(span, integration_config).set_status_code(600)

    assert span.error == 0


@pytest.mark.subprocess(env={"DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "400-599"})
def test_otel_span_attributes_explicit_default_client_status_does_not_expand():
    from unittest import mock

    from ddtrace._trace.otel.http.tags import OTelHTTPSpanAttributes
    from ddtrace.constants import SPAN_KIND
    from ddtrace.ext import SpanKind
    from ddtrace.ext import SpanTypes
    from ddtrace.trace import Span

    integration_config = mock.Mock(http_tag_query_string=False, trace_query_string=False)
    span = Span("http.request", span_type=SpanTypes.HTTP)
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)

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


@pytest.mark.subprocess(
    env={
        "DD_TRACE_OTEL_SEMANTICS_ENABLED": "true",
        "OTEL_TRACES_EXPORTER": "none",
        "DD_TRACE_AGENT_PROTOCOL_VERSION": "v0.4",
        "DD_TRACE_API_VERSION": "v0.4",
    },
    err=None,
)
def test_otel_semantics_flag_resolves_identically_when_enabled():
    from ddtrace.internal.settings._agent import config as agent_config
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.settings._opentelemetry import _is_otlp_traces_exporter_enabled
    from ddtrace.internal.settings._opentelemetry import otel_config
    from ddtrace.trace import tracer

    assert config._otel_trace_semantics_enabled is True
    assert _is_otlp_traces_exporter_enabled(otel_config.exporter) is True
    assert agent_config.trace_native_span_events is True
    assert tracer._span_aggregator.writer._otlp_endpoint == otel_config.exporter.TRACES_ENDPOINT


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_SEMANTICS_ENABLED": "false"}, err=None)
def test_otel_semantics_flag_resolves_identically_when_disabled():
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.settings._opentelemetry import _is_otlp_traces_exporter_enabled
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert config._otel_trace_semantics_enabled is False
    assert _is_otlp_traces_exporter_enabled(otel_config.exporter) is False


@pytest.mark.parametrize("span_type, span_kind", [(SpanTypes.WEB, None), (SpanTypes.HTTP, SpanKind.CLIENT)])
def test_otel_span_attributes_malformed_url_is_not_logged(tracer, integration_config, span_type, span_kind):
    with tracer.trace("request", span_type=span_type) as span:
        if span_kind is not None:
            span._set_attribute(SPAN_KIND, span_kind)
        attributes = OTelHTTPSpanAttributes(span, integration_config)

        with mock.patch.object(tags.log, "debug") as debug:
            attributes.set_url("http://user:hunter2@[::1/path?token=secret")

    debug.assert_called_once()
    logged = " ".join(str(arg) for arg in debug.call_args.args)
    assert "failed to parse http url" in logged
    assert "hunter2" not in logged
    assert "secret" not in logged


def test_set_http_meta_http_span_without_kind_keeps_legacy_tags(tracer, integration_config):
    # Ray Serve proxy requests are HTTP spans with no span.kind and must not be treated as clients.
    with tracer.trace("proxy_request", span_type=SpanTypes.HTTP) as span:
        with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
            trace_utils.set_http_meta(
                span, integration_config, method="GET", url="http://example.com/x", status_code=200
            )

    assert span.get_tag(otel_http.URL_FULL) is None
