from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanTypes
from ddtrace.ext import http


# The only target key the naming algorithm reads, per span kind. url.template is opt-in and
# still in development upstream, so no integration emits it today and client spans come out as
# the bare method. Reading it anyway means the name becomes {method} {url.template} for free
# whenever an integration starts publishing it.
_TARGET_TAG = {
    "server": http.OTEL_ROUTE,
    "client": "url.template",
}

_HTTP_SPAN_TYPES = (SpanTypes.WEB, SpanTypes.HTTP)

# An ASGI websocket handshake is reported with method "websocket", which is not an HTTP method
# and so normalizes to _OTHER. Renaming those would collapse every websocket endpoint in an app
# onto one name, so they keep whatever the integration called them.
_WEBSOCKET_METHOD = "websocket"

# Set by the integrations that let a user own the resource name, so that ownership survives here.
RESOURCE_SET_BY_USER = "_dd.resource_set_by_user"


class OtelSpanNamingProcessor(SpanProcessor):
    """Derive the HTTP span name from the span's own attributes under OTel semantics.

    Each integration composes a resource name its own way: Django joins the method with the
    resolved route, falls back to the view handler when there is none, and normalizes a
    Resolver404 to "GET 404". Those are reasonable Datadog resource names and none of them are
    valid OTel span names, so under the flag the name is recomputed here rather than patched
    integration by integration.

    The algorithm is the OpenTelemetry Collector's set_semconv_span_name: read
    http.request.method, append the target if the span published one, and use the bare method
    otherwise. Two properties follow from deriving rather than composing. The target can never
    be something the span does not also publish as an attribute, which is what makes
    "instrumentation MUST NOT default to using URI path as a target" unreachable instead of
    merely forbidden. And the method token is whatever the attribute says, including _OTHER for
    a method outside the accepted set, so the raw verb cannot leak into the name.

    Runs on span finish rather than inside set_http_meta because the attributes are not all
    final at that point: starlette resolves its route in a later set_http_meta call, and several
    integrations write the resource at more than one point in the request.
    """

    def on_span_start(self, span: Span) -> None:
        pass

    def on_span_finish(self, span: Span) -> None:
        if span.span_type not in _HTTP_SPAN_TYPES:
            return

        method = span.get_tag(http.OTEL_REQUEST_METHOD)
        if not method:
            # Not an HTTP span the semantics layer touched. Plenty of spans carry
            # SpanTypes.HTTP without going through set_http_meta, the AWS SDK and consul
            # client spans among them, and renaming those to a bare method would be nonsense.
            return

        if span.get_tag(http.OTEL_REQUEST_METHOD_ORIGINAL) == _WEBSOCKET_METHOD:
            return

        if span._get_ctx_item(RESOURCE_SET_BY_USER):
            # The user named this span themselves. That wins here for the same reason
            # Span.update_name wins over instrumentation in the OTel SDKs.
            return

        target = self._target(span)
        span.resource = f"{method} {target}" if target else method

    def _target(self, span: Span) -> Optional[str]:
        target_tag = _TARGET_TAG.get(span.get_tag(SPAN_KIND) or "")
        return span.get_tag(target_tag) if target_tag else None
