# Import subscriber modules to trigger auto-registration via __init_subclass__
import ddtrace._trace.subscribers.aiohttp  # noqa: F401
import ddtrace._trace.subscribers.http_client  # noqa: F401
import ddtrace._trace.subscribers.molten  # noqa: F401
import ddtrace._trace.subscribers.web_framework  # noqa: F401
