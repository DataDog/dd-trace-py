# Import subscriber packages — triggers auto-registration via __init_subclass__
import ddtrace._trace.subscribers.http_client  # noqa: F401
import ddtrace._trace.subscribers.llm  # noqa: F401
import ddtrace._trace.subscribers.messaging  # noqa: F401
import ddtrace._trace.subscribers.web_framework  # noqa: F401
