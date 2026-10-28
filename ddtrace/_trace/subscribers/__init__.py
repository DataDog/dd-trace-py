# Import subscriber packages — triggers auto-registration via __init_subclass__
from ddtrace._trace._registration import registration
import ddtrace._trace.subscribers.http_client  # noqa: F401
import ddtrace._trace.subscribers.kafka  # noqa: F401
import ddtrace._trace.subscribers.llm  # noqa: F401
import ddtrace._trace.subscribers.messaging  # noqa: F401
import ddtrace._trace.subscribers.molten  # noqa: F401


# Web subscribers are explicitly registered through the tracing product declaration.
registration.register()
