from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanTypes
from ddtrace.ext import http


# The only target key the algorithm reads, per span kind. No integration emits url.template yet,
# so client spans come out as the bare method until one does.
_TARGET_TAG = {
    "server": http.OTEL_ROUTE,
    "client": "url.template",
}

_HTTP_SPAN_TYPES = (SpanTypes.WEB, SpanTypes.HTTP)

# An ASGI websocket handshake reports method "websocket", which normalizes to _OTHER. Renaming
# those would collapse every websocket endpoint onto one name.
_WEBSOCKET_METHOD = "websocket"
_UNKNOWN_METHOD = "_OTHER"
_UNKNOWN_METHOD_SPAN_NAME = "HTTP"

# Django treats any value replacing this sentinel as user-owned. Honour that during early
# sampling rather than inferring ownership from the resource's shape.
_DJANGO_REQUEST_SPAN_NAME = "django.request"
_DJANGO_REQUEST_DEFAULT_RESOURCE = "__django_request"

# Set by the integrations that let a user own the resource name, so that ownership survives here.
RESOURCE_SET_BY_USER = "_dd.resource_set_by_user"
# The resource an integration last wrote, so this processor can tell its own value apart from a
# user replacement. The value is whatever the integration composed, not necessarily an OTel name.
INSTRUMENTATION_HTTP_RESOURCE = "_dd.instrumentation_http_resource"


def otel_http_resource(method: str, target: Optional[str]) -> str:
    """Compose the OTel HTTP resource from a normalized method and an optional target.

    Shared with the integrations that resolve their target early enough to name the span
    before sampling. Both paths must produce the same string: `before_sampling` tells its own
    value apart from a user-set resource by comparing them, so any drift here would make the
    processor mistake its own name for a user's and stop renaming.
    """
    method_token = _UNKNOWN_METHOD_SPAN_NAME if method == _UNKNOWN_METHOD else method
    return f"{method_token} {target}" if target else method_token


class OtelSpanNamingProcessor(SpanProcessor):
    """Derive the HTTP span name from the span's own attributes under OTel semantics.

    Recomputed here rather than patched integration by integration: each one composes a Datadog
    resource its own way, and none of those are valid OTel span names.

    The algorithm is the OpenTelemetry Collector's set_semconv_span_name. Deriving the target
    from an attribute the span already published is what makes "instrumentation MUST NOT default
    to using URI path as a {target}" unreachable rather than merely forbidden.

    Runs on span finish because the attributes are not final earlier: starlette resolves its
    route in a later set_http_meta call.
    """

    def on_span_start(self, span: Span) -> None:
        pass

    def before_sampling(self, span: Span) -> None:
        # Sampling can run before the framework resolves http.route, notably for Django, so a
        # rule sees the method-only name and the finish pass appends the route later.
        if span._get_ctx_item(RESOURCE_SET_BY_USER):
            return

        generated_resource = span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE)
        if (
            span.name == _DJANGO_REQUEST_SPAN_NAME
            and span.resource != _DJANGO_REQUEST_DEFAULT_RESOURCE
            and span.resource != generated_resource
        ):
            return

        method = span.get_tag(http.OTEL_REQUEST_METHOD)
        if not method:
            return

        if span.name != _DJANGO_REQUEST_SPAN_NAME:
            if span.resource and span.resource != span.name and span.resource != generated_resource:
                # Only integrations set the marker, so a different resource is user code's.
                span._set_ctx_item(RESOURCE_SET_BY_USER, True)
                return

        self.on_span_finish(span)

    def on_span_finish(self, span: Span) -> None:
        if span.span_type not in _HTTP_SPAN_TYPES:
            return

        method = span.get_tag(http.OTEL_REQUEST_METHOD)
        if not method:
            # Plenty of spans carry SpanTypes.HTTP without going through set_http_meta, the
            # AWS SDK and consul clients among them; a bare method would be nonsense there.
            return

        if span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == _WEBSOCKET_METHOD:
            return

        if span._get_ctx_item(RESOURCE_SET_BY_USER):
            # Wins for the same reason Span.update_name wins over instrumentation in an OTel SDK.
            return

        generated_resource = span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE)
        if span.resource and span.resource != span.name and span.resource != generated_resource:
            # Shape is deliberately irrelevant: "GET /custom" is a valid explicit user name.
            span._set_ctx_item(RESOURCE_SET_BY_USER, True)
            return

        resource = otel_http_resource(method, self._target(span))
        span.resource = resource
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)

    def _target(self, span: Span) -> Optional[str]:
        target_tag = _TARGET_TAG.get(span.get_tag(SPAN_KIND) or "")
        return span.get_tag(target_tag) if target_tag else None
