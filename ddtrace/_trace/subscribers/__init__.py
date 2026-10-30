# Import subscriber packages — triggers auto-registration via __init_subclass__
import ddtrace._trace.subscribers.http_client  # noqa: F401
import ddtrace._trace.subscribers.kafka  # noqa: F401
import ddtrace._trace.subscribers.llm  # noqa: F401
import ddtrace._trace.subscribers.messaging  # noqa: F401
import ddtrace._trace.subscribers.molten  # noqa: F401
from ddtrace._trace.subscribers.web_framework import WebFrameworkRequestSubscriber


# Direct integration imports also need listeners when the product lifecycle has not run.
WebFrameworkRequestSubscriber.register()
