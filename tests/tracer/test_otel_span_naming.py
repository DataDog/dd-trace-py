import pytest

from ddtrace._trace.processor.otel_span_naming import RESOURCE_SET_BY_USER
from ddtrace._trace.processor.otel_span_naming import OtelSpanNamingProcessor
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.trace import Span


def _finish(span_type=SpanTypes.WEB, resource="composed by the integration", **tags):
    span = Span("django.request", resource=resource, span_type=span_type)
    for key, value in tags.items():
        span._set_attribute(key, value)
    OtelSpanNamingProcessor().on_span_finish(span)
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
        """_OTHER replaces the verb, and a resolved route still appends after it."""
        tags = {SPAN_KIND: "server", http.OTEL_REQUEST_METHOD: "_OTHER"}
        if route:
            tags[http.OTEL_ROUTE] = route
        assert _finish(**tags) == ("_OTHER /users" if route else "_OTHER")

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
            == "_OTHER /some/<path>"
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


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_SEMANTICS_ENABLED": "true"})
def test_processor_is_installed_when_the_flag_is_on():
    from ddtrace._trace.processor.otel_span_naming import OtelSpanNamingProcessor
    from ddtrace.trace import tracer

    assert any(isinstance(p, OtelSpanNamingProcessor) for p in tracer._span_processors)


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_SEMANTICS_ENABLED": "false"})
def test_processor_is_absent_when_the_flag_is_off():
    """The flag is read once at startup, so the wiring is the whole opt-in."""
    from ddtrace._trace.processor.otel_span_naming import OtelSpanNamingProcessor
    from ddtrace.trace import tracer

    assert not any(isinstance(p, OtelSpanNamingProcessor) for p in tracer._span_processors)
