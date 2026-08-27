from unittest import mock

import pytest

from ddtrace._trace.otel_http_naming import INSTRUMENTATION_HTTP_RESOURCE
from ddtrace._trace.otel_http_naming import RESOURCE_SET_BY_USER
from ddtrace._trace.otel_http_naming import set_otel_http_resource
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.trace import tracer


_INTEGRATION_RESOURCE = "composed by the integration"


def _integration_config():
    return IntegrationConfig(config, "test")


def _name(
    method=None,
    route=None,
    kind="server",
    span_type=SpanTypes.WEB,
    resource=_INTEGRATION_RESOURCE,
    name="django.request",
    span_hook=None,
    **tags,
):
    """Name a span the way an integration does, and return the resulting resource.

    set_http_meta is the only writer of the OTel name, so the tests drive it rather than a
    processor: the span leaves the integration already named.
    """
    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        with tracer.start_span(name, span_type=span_type, activate=False) as span:
            if resource is not None:
                span.resource = resource
                span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)
            if kind is not None:
                span._set_attribute(SPAN_KIND, kind)
            for key, value in tags.items():
                span._set_attribute(key, value)
            if span_hook is not None:
                span_hook(span)
            trace_utils._set_http_meta_otel(span, _integration_config(), method=method, route=route)
            return span.resource


class TestOtelSpanNaming:
    def test_server_with_route(self):
        assert _name(method="GET", route="/users/<int:id>") == "GET /users/<int:id>"

    def test_server_without_route_is_bare_method(self):
        """The target is appended only when the integration resolved one, never invented."""
        assert _name(method="GET") == "GET"

    def test_server_without_route_ignores_the_uri_path(self):
        """The MUST NOT is unreachable rather than merely forbidden.

        The name is composed from the method and route the caller passes, so it cannot fall
        back to the URI path however the integration composed its own resource.
        """
        assert _name(method="GET", resource="GET /users/12345") == "GET"

    @pytest.mark.parametrize("route", [None, "/ping"])
    def test_unaccepted_method_substitutes_only_the_method_token(self, route):
        expected = "HTTP /ping" if route else "HTTP"
        assert _name(method="FROBNICATE", route=route) == expected

    def test_raw_method_never_leaks_into_the_name(self):
        """A lowercase method normalizes, and the name uses the normalized token."""
        assert _name(method="get") == "GET"

    def test_user_set_resource_is_preserved(self):
        """Wins for the same reason Span.update_name wins over instrumentation in an OTel SDK."""

        def claim(span):
            span._set_ctx_item(RESOURCE_SET_BY_USER, True)

        assert _name(method="GET", route="/users", span_hook=claim) == _INTEGRATION_RESOURCE

    def test_resource_replaced_by_user_code_is_detected_and_kept(self):
        """A resource matching neither the span name nor the marker is user code's."""
        assert _name(method="GET", route="/users", resource=None, span_hook=_set_custom) == "my custom name"

    def test_websocket_handshake_is_left_alone(self):
        """Renaming these would collapse every websocket endpoint onto one name."""
        assert _name(method="websocket") == _INTEGRATION_RESOURCE

    def test_client_is_bare_method_without_url_template(self):
        """No integration emits url.template, so client spans are the bare method."""
        assert _name(method="GET", kind="client", span_type=SpanTypes.HTTP) == "GET"

    def test_client_does_not_take_http_route_as_its_target(self):
        """http.route is a server concept; a client span must not borrow it."""
        assert _name(method="GET", route="/users/<int:id>", kind="client", span_type=SpanTypes.HTTP) == "GET"

    def test_span_without_a_method_is_left_alone(self):
        """Plenty of spans reach set_http_meta with no method; a bare method would be nonsense."""
        assert _name(method=None) == _INTEGRATION_RESOURCE

    def test_span_with_no_kind_gets_the_bare_method(self):
        assert _name(method="GET", kind=None) == "GET"

    def test_a_later_call_refines_the_name_with_a_route(self):
        """starlette resolves its route in a second set_http_meta call, and the later name wins."""
        with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
            with tracer.start_span("web.request", span_type=SpanTypes.WEB, activate=False) as span:
                span.resource = _INTEGRATION_RESOURCE
                span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, _INTEGRATION_RESOURCE)
                span._set_attribute(SPAN_KIND, "server")
                ic = _integration_config()
                trace_utils._set_http_meta_otel(span, ic, method="GET")
                assert span.resource == "GET"
                trace_utils._set_http_meta_otel(span, ic, method="GET", route="/users/<int:id>")
                assert span.resource == "GET /users/<int:id>"

    def test_a_user_resource_set_between_calls_stops_the_rename(self):
        with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
            with tracer.start_span("web.request", span_type=SpanTypes.WEB, activate=False) as span:
                span.resource = _INTEGRATION_RESOURCE
                span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, _INTEGRATION_RESOURCE)
                span._set_attribute(SPAN_KIND, "server")
                ic = _integration_config()
                trace_utils._set_http_meta_otel(span, ic, method="GET")
                span.resource = "my custom name"
                trace_utils._set_http_meta_otel(span, ic, method="GET", route="/users")
                assert span.resource == "my custom name"
                assert span._get_ctx_item(RESOURCE_SET_BY_USER) is True

    def test_the_method_attribute_is_still_written(self):
        """Naming is additive: the attributes the conventions require are unaffected."""
        with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
            with tracer.start_span("web.request", span_type=SpanTypes.WEB, activate=False) as span:
                span._set_attribute(SPAN_KIND, "server")
                trace_utils._set_http_meta_otel(span, _integration_config(), method="get", route="/x")
                assert span.get_tag(http.OTEL_REQUEST_METHOD) == "GET"
                assert span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == "get"
                assert span.get_tag(http.OTEL_ROUTE) == "/x"


class TestNamedBeforeSampling:
    """Propagation can force a sampling decision before the request finishes.

    A rule must match the OTel resource, so the subscribers name the span at start rather than
    leaving the integration's Datadog value in place until set_http_meta runs.
    """

    def test_server_span_is_named_at_start_not_left_as_the_integration_resource(self):
        from ddtrace.contrib.internal.trace_utils_base import normalize_http_method

        with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
            with tracer.start_span("aiohttp.request", span_type=SpanTypes.WEB, activate=False) as span:
                span.resource = "aiohttp.request"
                span._set_attribute(SPAN_KIND, "server")
                normalized, original = normalize_http_method("GET")
                set_otel_http_resource(span, normalized, original)
                assert span.resource == "GET", "sampling would otherwise see the Datadog resource"
                trace_utils._set_http_meta_otel(span, _integration_config(), method="GET", route="/users/{id}")
                assert span.resource == "GET /users/{id}"

    def test_unaccepted_client_method_is_normalized_at_start(self):
        """Sampling must not see PROPFIND when the span ships as HTTP."""
        from ddtrace.contrib.internal.trace_utils_base import normalize_http_method

        with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
            with tracer.start_span("http.request", span_type=SpanTypes.HTTP, activate=False) as span:
                span._set_attribute(SPAN_KIND, "client")
                normalized, original = normalize_http_method("PROPFIND")
                set_otel_http_resource(span, normalized, original)
                at_sampling = span.resource
                trace_utils._set_http_meta_otel(span, _integration_config(), method="PROPFIND")
                assert at_sampling == "HTTP"
                assert span.resource == at_sampling


def _set_custom(span):
    span.resource = "my custom name"


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_SEMANTICS_ENABLED": "true"}, err=None)
def test_event_supplied_resource_is_replaced_not_treated_as_user_owned():
    """Integrations supply a resource when they create the span, and it must still be renamed.

    requests passes resource="GET /actual/path" through HttpClientRequestEvent. Reading that as
    a user's choice would ship the URI path as the span name, which the conventions forbid, and
    would do so at sampling time as well as at export.
    """
    from ddtrace.contrib._events.http_client import HttpClientRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.span_bus import span_from_context

    for method, expected in (("GET", "GET"), ("PROPFIND", "HTTP"), ("get", "GET")):
        with core.context_with_event(
            HttpClientRequestEvent(
                http_operation="requests.request",
                service="svc",
                component="requests",
                resource="{} /actual/path/42".format(method),
                integration_config=config.requests,
                request_method=method,
                request_headers={},
                query="",
                request_url="http://host/actual/path/42",
            ),
        ) as ctx:
            span = span_from_context(ctx)
            # The subscriber has run, so this is what a sampling rule would match on.
            assert span.resource == expected, "{} gave {!r}".format(method, span.resource)
            assert "/actual/path/42" not in span.resource


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_SEMANTICS_ENABLED": "true"}, err=None)
def test_a_user_replacing_the_resource_after_start_still_wins():
    """The ownership marker must not turn into a blanket override of user intent."""
    from ddtrace.contrib._events.http_client import HttpClientRequestEvent
    from ddtrace.contrib.internal import trace_utils
    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.span_bus import span_from_context

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="requests.request",
            service="svc",
            component="requests",
            resource="GET /actual/path/42",
            integration_config=config.requests,
            request_method="GET",
            request_headers={},
            query="",
            request_url="http://host/actual/path/42",
        ),
    ) as ctx:
        span = span_from_context(ctx)
        assert span.resource == "GET"
        span.resource = "my custom name"
        trace_utils.set_http_meta(span, config.requests, method="GET")
        assert span.resource == "my custom name"


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
        "OTEL_TRACES_EXPORTER": None,
        "DD_TRACE_AGENT_PROTOCOL_VERSION": "v0.4",
    }
)
def test_otel_semantics_forces_otlp_trace_export_over_agent_protocol():
    from ddtrace.internal.settings._agent import config as agent_config

    assert agent_config.trace_otlp_export_enabled is True
