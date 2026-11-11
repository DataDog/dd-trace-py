# Import subscriber packages — triggers auto-registration via __init_subclass__
from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
import ddtrace._trace.subscribers.http_client  # noqa: F401
import ddtrace._trace.subscribers.kafka  # noqa: F401
import ddtrace._trace.subscribers.llm  # noqa: F401
import ddtrace._trace.subscribers.messaging  # noqa: F401
import ddtrace._trace.subscribers.molten  # noqa: F401
from ddtrace.trace import tracer


# Preserve manual instrumentation through tracing's existing subscriber initialization.
# Product startup also registers this bundle; integration setup never owns its activation.
if not tracer._shutting_down:
    WEB_FRAMEWORK_SUBSCRIBERS.register()
