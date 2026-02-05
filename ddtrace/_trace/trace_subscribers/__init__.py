from ddtrace._trace.trace_subscribers._base import SpanTracingSubscriber  # noqa: F401
from ddtrace._trace.trace_subscribers._base import TracingSubscriber  # noqa: F401

# Import subscriber modules to trigger auto-registration via __init_subclass__
import ddtrace._trace.trace_subscribers.http_client  # noqa: F401
