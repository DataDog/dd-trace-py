"""
From agent truncators: https://github.com/DataDog/datadog-agent/blob/main/pkg/trace/agent/truncator.go
"""

from . import SpanProcessor


# Values from:
# https://github.com/DataDog/datadog-agent/blob/ff91f174c6534be2b6f24baddc71eaec9224b0fc/pkg/trace/traceutil/truncate.go#L22-L27

MAX_RESOURCE_NAME_LENGTH = 5000
"""MAX_RESOURCE_NAME_LENGTH the maximum length a span resource can have."""

MAX_META_KEY_LENGTH = 200
"""MAX_META_KEY_LENGTH the maximum length of metadata key."""

MAX_META_VALUE_LENGTH = 25000
"""MAX_META_VALUE_LENGTH the maximum length of metadata value."""

MAX_METRIC_KEY_LENGTH = MAX_META_KEY_LENGTH
"""MAX_METRIC_KEY_LENGTH the maximum length of a metric name key."""

# From agent normalizer:
# https://github.com/DataDog/datadog-agent/blob/ff91f174c6534be2b6f24baddc71eaec9224b0fc/pkg/trace/traceutil/normalize.go#L23-L28

DEFAULT_SPAN_NAME = "unnamed_operation"
"""DEFAULT_SPAN_NAME is the default name we assign a span if it's missing and we have no reasonable fallback."""

DEFAULT_SERVICE_NAME = "unnamed-service"
"""DEFAULT_SERVICE_NAME is the default name we assign a service if it's missing and we have no reasonable fallback."""


MAX_NAME_LENGTH = 100
"""MAX_NAME_LENGTH the maximum length a name can have."""

MAX_SERVICE_LENGTH = 100
"""MAX_SERVICE_LENGTH the maximum length a service can have."""

MAX_TYPE_LENGTH = 100
"""MAX_TYPE_LENGTH the maximum length a span type can have."""


def truncate_to_length(value, max_length):
    """Truncate a string to a maximum length."""
    if not value or len(value) <= max_length:
        return value

    return value[:max_length]


class TruncateSpanProcessor(SpanProcessor):
    def on_span_start(self, span):
        pass

    def on_span_finish(self, span):
        span.resource = truncate_to_length(span.resource, MAX_RESOURCE_NAME_LENGTH)
        span._meta = {
            truncate_to_length(k, MAX_META_KEY_LENGTH): truncate_to_length(v, MAX_META_VALUE_LENGTH)
            for k, v in span._meta.items()
        }
        span._metrics = {truncate_to_length(k, MAX_METRIC_KEY_LENGTH): v for k, v in span._metrics.items()}


class NormalizeSpanProcessor(SpanProcessor):
    def on_span_start(self, span):
        pass

    def on_span_finish(self, span):
        span.service = truncate_to_length(span.service or DEFAULT_SERVICE_NAME, MAX_SERVICE_LENGTH)
        span.name = truncate_to_length(span.name or DEFAULT_SPAN_NAME, MAX_NAME_LENGTH)
        if not span.resource:
            span.resource = span.name
        if span.span_type:
            span.span_type = span.span_type[:MAX_TYPE_LENGTH]
