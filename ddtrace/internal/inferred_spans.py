"""Names shared by inferred-span producers and consumers."""

from ddtrace.internal.native._native import SpanData


PROXY_SPAN_NAMES = {
    "aws-apigateway": "aws.apigateway",
    "aws-httpapi": "aws.httpapi",
    "azure-apim": "azure.apim",
    "azure-frontdoor": "azure.frontdoor",
}
PUBSUB_PUSH_SPAN_NAME = "gcp.pubsub.receive"

# Span names for supported proxy systems (API Gateway, etc.).
SUPPORTED_PROXY_SPAN_NAMES = set(PROXY_SPAN_NAMES.values())

# Span names for push-based subscriptions (e.g., GCP Pub/Sub push).
PUSH_SUBSCRIPTION_SPAN_NAMES = {PUBSUB_PUSH_SPAN_NAME}

# Span names for synthetically created inferred spans.
# AppSec uses this to report security signals on the actual service span in addition to the inferred parent.
# Trace handlers use this to propagate HTTP status codes and errors from child spans up to the inferred parent.
INFERRED_SPAN_NAMES = SUPPORTED_PROXY_SPAN_NAMES | PUSH_SUBSCRIPTION_SPAN_NAMES


def is_inferred_span(span: SpanData) -> bool:
    return span.name in INFERRED_SPAN_NAMES
