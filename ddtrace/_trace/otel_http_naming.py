from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.settings._config import config


# Renaming websocket handshakes would collapse every endpoint onto _OTHER.
_WEBSOCKET_METHOD = "websocket"
_UNKNOWN_METHOD = "_OTHER"
_UNKNOWN_METHOD_SPAN_NAME = "HTTP"

# These markers distinguish instrumentation-owned resources from user replacements.
RESOURCE_SET_BY_USER = "_dd.resource_set_by_user"
INSTRUMENTATION_HTTP_RESOURCE = "_dd.instrumentation_http_resource"


def otel_http_resource(method: str, target: Optional[str]) -> str:
    method_token = _UNKNOWN_METHOD_SPAN_NAME if method == _UNKNOWN_METHOD else method
    return f"{method_token} {target}" if target else method_token


def set_otel_http_resource(
    span: Span,
    normalized_method: str,
    original_method: Optional[str] = None,
    target: Optional[str] = None,
) -> None:
    """Name the span from emitted low-cardinality attributes, never a raw URI path."""
    if original_method == _WEBSOCKET_METHOD:
        return

    if span._get_ctx_item(RESOURCE_SET_BY_USER):
        return

    generated_resource = span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE)
    if span.resource and span.resource != span.name and span.resource != generated_resource:
        # A resource matching neither the span name nor our marker belongs to user code.
        span._set_ctx_item(RESOURCE_SET_BY_USER, True)
        return

    resource = otel_http_resource(normalized_method, target)
    span.resource = resource
    span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)


def record_initial_instrumentation_resource(span: Span, resource: Optional[str]) -> None:
    """Record resource ownership after span-start callbacks may replace it."""
    expected_resource = resource or span.name
    if span.resource == expected_resource:
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, span.resource)
    else:
        span._set_ctx_item(RESOURCE_SET_BY_USER, True)


def set_instrumentation_resource(span: Span, resource: str) -> None:
    """Set an instrumentation-owned resource without overriding a user replacement."""
    if config._otel_trace_semantics_enabled:
        if span._get_ctx_item(RESOURCE_SET_BY_USER):
            return

        previous_resource = span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE)
        if previous_resource is not None and span.resource != previous_resource:
            span._set_ctx_item(RESOURCE_SET_BY_USER, True)
            return

    span.resource = resource
    if config._otel_trace_semantics_enabled:
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)
