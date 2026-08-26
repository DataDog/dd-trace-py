from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.settings._config import config


# An ASGI websocket handshake reports method "websocket", which normalizes to _OTHER. Renaming
# those would collapse every websocket endpoint onto one name.
_WEBSOCKET_METHOD = "websocket"
_UNKNOWN_METHOD = "_OTHER"
_UNKNOWN_METHOD_SPAN_NAME = "HTTP"

# Set by the integrations that let a user own the resource name, so that ownership survives.
RESOURCE_SET_BY_USER = "_dd.resource_set_by_user"
# The resource an integration last wrote, so a later write can tell that value apart from a user
# replacement. The value is whatever the integration composed, not necessarily an OTel name.
INSTRUMENTATION_HTTP_RESOURCE = "_dd.instrumentation_http_resource"


def otel_http_resource(method: str, target: Optional[str]) -> str:
    """Compose the OTel HTTP resource from a normalized method and an optional target.

    The algorithm is the OpenTelemetry Collector's set_semconv_span_name. Callers pass a target
    the span already published, which is what makes "instrumentation MUST NOT default to using
    URI path as a {target}" unreachable rather than merely forbidden.
    """
    method_token = _UNKNOWN_METHOD_SPAN_NAME if method == _UNKNOWN_METHOD else method
    return f"{method_token} {target}" if target else method_token


def set_otel_http_resource(
    span: Span,
    normalized_method: str,
    original_method: Optional[str] = None,
    target: Optional[str] = None,
) -> None:
    """Name an HTTP span from the attributes the caller is already writing.

    Called from set_http_meta so the span carries its OTel name from the integration rather than
    being renamed later. Integrations that learn their route on a second call (starlette) simply
    call again, and the later, more specific name wins.
    """
    if original_method == _WEBSOCKET_METHOD:
        return

    if span._get_ctx_item(RESOURCE_SET_BY_USER):
        # Wins for the same reason Span.update_name wins over instrumentation in an OTel SDK.
        return

    generated_resource = span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE)
    if span.resource and span.resource != span.name and span.resource != generated_resource:
        # Only integrations set the marker, so a resource matching neither is user code's.
        # Shape is deliberately irrelevant: "GET /custom" is a valid explicit user name.
        span._set_ctx_item(RESOURCE_SET_BY_USER, True)
        return

    resource = otel_http_resource(normalized_method, target)
    span.resource = resource
    span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)


def set_instrumentation_resource(span: Span, resource: str) -> None:
    """Set a resource an integration composed, recording that instrumentation owns it.

    Integrations that write span.resource mid-request are otherwise indistinguishable from user
    code doing the same, and the later OTel naming would back off and leave the Datadog value
    on the span.
    """
    span.resource = resource
    if config._otel_trace_semantics_enabled:
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)
