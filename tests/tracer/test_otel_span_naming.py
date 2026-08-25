import pytest

from ddtrace._trace.processor.otel_span_naming import INSTRUMENTATION_HTTP_RESOURCE
from ddtrace._trace.processor.otel_span_naming import RESOURCE_SET_BY_USER
from ddtrace._trace.processor.otel_span_naming import OtelSpanNamingProcessor
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.trace import Span


def _finish(span_type=SpanTypes.WEB, resource="composed by the integration", **tags):
    span = Span("django.request", resource=resource, span_type=span_type)
    span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)
    processor = OtelSpanNamingProcessor()
    processor.on_span_start(span)
    for key, value in tags.items():
        span._set_attribute(key, value)
    processor.on_span_finish(span)
    return span.resource


class TestOtelSpanNaming:
    def test_server_with_route(self):
        assert (
            _finish(
                **{SPAN_KIND: "server", http.OTEL_REQUEST_METHOD: "GET", http.OTEL_ROUTE: "/users/<int:id>"},
            )
            == "GET /users/<int:id>"
        )

    def test_server_without_route_is_bare_method(self):
        """The target is appended only when the span published one, never invented."""
        assert _finish(**{SPAN_KIND: "server", http.OTEL_REQUEST_METHOD: "GET"}) == "GET"

    def test_server_without_route_ignores_the_uri_path(self):
        """The MUST NOT is unreachable rather than merely forbidden.

        url.path is on the span, and the algorithm never reads it, so a name derived this way
        cannot fall back to it however the integration composed its own resource.
        """
        assert (
            _finish(
                **{
                    SPAN_KIND: "server",
                    http.OTEL_REQUEST_METHOD: "GET",
                    http.OTEL_URL_PATH: "/no/such/route",
                },
            )
            == "GET"
        )

    @pytest.mark.parametrize("route", [None, "/users"])
    def test_unaccepted_method_substitutes_only_the_method_token(self, route):
        """HTTP replaces _OTHER in the name, and a resolved route still appends after it."""
        tags = {SPAN_KIND: "server", http.OTEL_REQUEST_METHOD: "_OTHER"}
        if route:
            tags[http.OTEL_ROUTE] = route
        assert _finish(**tags) == ("HTTP /users" if route else "HTTP")

    def test_raw_method_never_leaks_into_the_name(self):
        """The name comes from the attribute, so an unnormalized verb cannot reach it.

        Asserted on the whole name rather than as a substring negative, which would also pass
        for a name that is wrong in some other way.
        """
        assert (
            _finish(
                resource="PROPFIND /some/path",
                **{
                    SPAN_KIND: "server",
                    http.OTEL_REQUEST_METHOD: "_OTHER",
                    http.OTEL_REQUEST_METHOD_ORIGINAL: "PROPFIND",
                    http.OTEL_ROUTE: "/some/<path>",
                },
            )
            == "HTTP /some/<path>"
        )

    def test_user_set_resource_is_preserved(self):
        """An integration that lets the user own the name says so, and that ownership wins."""
        span = Span("django.request", resource="my own name", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_attribute(http.OTEL_ROUTE, "/users")
        span._set_ctx_item(RESOURCE_SET_BY_USER, True)
        OtelSpanNamingProcessor().on_span_finish(span)
        assert span.resource == "my own name"

    def test_websocket_handshake_is_left_alone(self):
        """ASGI reports "websocket" as the method, which is not one, so it normalizes to _OTHER.

        Renaming those would collapse every websocket endpoint in an app onto a single name.
        """
        assert (
            _finish(
                resource="websocket /ws/chat",
                **{
                    SPAN_KIND: "server",
                    http.OTEL_REQUEST_METHOD: "_OTHER",
                    http.OTEL_REQUEST_METHOD_ORIGINAL: "websocket",
                },
            )
            == "websocket /ws/chat"
        )

    def test_client_is_bare_method_without_url_template(self):
        """No integration emits url.template today, so every client span is the method."""
        assert (
            _finish(
                span_type=SpanTypes.HTTP,
                **{SPAN_KIND: "client", http.OTEL_REQUEST_METHOD: "GET", http.OTEL_URL_FULL: "http://host/a/b"},
            )
            == "GET"
        )

    def test_client_appends_url_template_when_present(self):
        assert (
            _finish(
                span_type=SpanTypes.HTTP,
                **{SPAN_KIND: "client", http.OTEL_REQUEST_METHOD: "GET", "url.template": "/users/{id}"},
            )
            == "GET /users/{id}"
        )

    def test_client_does_not_take_http_route_as_its_target(self):
        """http.route is the server target. Reading it on a client span would be the wrong key."""
        assert (
            _finish(
                span_type=SpanTypes.HTTP,
                **{SPAN_KIND: "client", http.OTEL_REQUEST_METHOD: "GET", http.OTEL_ROUTE: "/users"},
            )
            == "GET"
        )

    def test_span_without_the_otel_method_is_left_alone(self):
        """A cache or template span can carry SpanTypes.HTTP; renaming those would be nonsense."""
        assert _finish(span_type=SpanTypes.HTTP, resource="GET myprefix") == "GET myprefix"

    def test_non_http_span_is_left_alone(self):
        assert _finish(span_type=SpanTypes.SQL, resource="SELECT 1", **{http.OTEL_REQUEST_METHOD: "GET"}) == "SELECT 1"

    def test_span_with_no_kind_gets_the_bare_method(self):
        """No kind means no target key applies, so nothing is appended rather than guessed."""
        assert _finish(resource="GET /whatever", **{http.OTEL_REQUEST_METHOD: "GET"}) == "GET"

    def test_before_sampling_preserves_an_existing_otel_name(self):
        span = Span("flask.request", resource="GET /make_distant_call", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_attribute(http.OTEL_ROUTE, "/make_distant_call")
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)

        OtelSpanNamingProcessor().before_sampling(span)

        assert span.resource == "GET /make_distant_call"

    def test_before_sampling_normalizes_an_unknown_method(self):
        span = Span("flask.request", resource="PROPFIND 405", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "_OTHER")
        span._set_attribute(http.OTEL_REQUEST_METHOD_ORIGINAL, "PROPFIND")
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)

        OtelSpanNamingProcessor().before_sampling(span)

        assert span.resource == "HTTP"

    def test_before_sampling_replaces_django_sentinel_with_method_only_resource(self):
        span = Span("django.request", resource="__django_request", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)

        OtelSpanNamingProcessor().before_sampling(span)

        assert span.resource == "GET"
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "GET"
        assert span._get_ctx_item(RESOURCE_SET_BY_USER) is None

        span._set_attribute(http.OTEL_ROUTE, "/users/<int:id>")
        OtelSpanNamingProcessor().on_span_finish(span)
        assert span.resource == "GET /users/<int:id>"

    def test_before_sampling_preserves_django_explicit_user_resource(self):
        span = Span("django.request", resource="my own name", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")

        OtelSpanNamingProcessor().before_sampling(span)

        assert span.resource == "my own name"
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) is None

    def test_before_sampling_preserves_generic_explicit_user_resource(self):
        span = Span("wsgi.request", resource="checkout-flow", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")

        processor = OtelSpanNamingProcessor()
        processor.before_sampling(span)
        processor.on_span_finish(span)

        assert span.resource == "checkout-flow"
        assert span._get_ctx_item(RESOURCE_SET_BY_USER) is True

    def test_finish_preserves_custom_resource_set_after_sampling(self):
        span = Span("wsgi.request", resource="GET /users/1", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)

        processor = OtelSpanNamingProcessor()
        processor.before_sampling(span)
        assert span.resource == "GET"

        span.resource = "checkout-flow"
        span._set_attribute(http.OTEL_ROUTE, "/users/<id>")
        processor.on_span_finish(span)

        assert span.resource == "checkout-flow"
        assert span._get_ctx_item(RESOURCE_SET_BY_USER) is True

    def test_finish_preserves_custom_resource_without_early_sampling(self):
        span = Span("wsgi.request", resource="wsgi.request", span_type=SpanTypes.WEB)
        processor = OtelSpanNamingProcessor()
        processor.on_span_start(span)

        span.resource = "checkout-flow"
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_attribute(http.OTEL_ROUTE, "/users/<id>")

        processor.on_span_finish(span)

        assert span.resource == "checkout-flow"
        assert span._get_ctx_item(RESOURCE_SET_BY_USER) is True

    def test_finish_normalizes_instrumentation_route_set_after_sampling(self):
        span = Span("wsgi.request", resource="GET /users/1", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)

        processor = OtelSpanNamingProcessor()
        processor.before_sampling(span)
        assert span.resource == "GET"

        span.resource = "GET /users/<id>"
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)
        span._set_attribute(http.OTEL_ROUTE, "/users/<id>")
        processor.on_span_finish(span)

        assert span.resource == "GET /users/<id>"
        assert span._get_ctx_item(RESOURCE_SET_BY_USER) is None

    def test_before_sampling_does_not_infer_http_metadata_from_manual_resource(self):
        span = Span("manual.request", resource="GET /checkout", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")

        processor = OtelSpanNamingProcessor()
        processor.before_sampling(span)
        processor.on_span_finish(span)

        assert span.resource == "GET /checkout"
        assert span.get_tag(http.OTEL_REQUEST_METHOD) is None
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) is None

    def test_before_sampling_normalizes_framework_placeholder_resource(self):
        span = Span("pyramid.request", resource="404", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, "404")

        OtelSpanNamingProcessor().before_sampling(span)

        assert span.resource == "GET"
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) == "GET"

    def test_finish_normalizes_late_framework_placeholder_resource(self):
        span = Span("aiohttp.request", resource="aiohttp.request", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")
        span._set_attribute(http.OTEL_REQUEST_METHOD, "GET")
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, "aiohttp.request")

        span.resource = "404"
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, "404")
        OtelSpanNamingProcessor().on_span_finish(span)

        assert span.resource == "GET"
        assert span._get_ctx_item(RESOURCE_SET_BY_USER) is None

    def test_before_sampling_does_not_infer_method_from_custom_resource(self):
        span = Span("wsgi.request", resource="GET checkout-flow", span_type=SpanTypes.WEB)
        span._set_attribute(SPAN_KIND, "server")

        processor = OtelSpanNamingProcessor()
        processor.before_sampling(span)
        processor.on_span_finish(span)

        assert span.resource == "GET checkout-flow"
        assert span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE) is None


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_SEMANTICS_ENABLED": "true"})
def test_processor_is_installed_when_the_flag_is_on():
    from ddtrace._trace.processor.otel_span_naming import OtelSpanNamingProcessor
    from ddtrace._trace.processor.resource_renaming import ResourceRenamingProcessor
    from ddtrace.trace import tracer

    assert any(isinstance(p, OtelSpanNamingProcessor) for p in tracer._span_processors)
    # http.endpoint belongs to resource renaming, which this flag must not switch on.
    assert not any(isinstance(p, ResourceRenamingProcessor) for p in tracer._span_processors)


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_SEMANTICS_ENABLED": "false"})
def test_processor_is_absent_when_the_flag_is_off():
    """The flag is read once at startup, so the wiring is the whole opt-in."""
    from ddtrace._trace.processor.otel_span_naming import OtelSpanNamingProcessor
    from ddtrace.trace import tracer

    assert not any(isinstance(p, OtelSpanNamingProcessor) for p in tracer._span_processors)


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
