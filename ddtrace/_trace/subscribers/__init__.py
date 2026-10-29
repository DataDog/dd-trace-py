# Import subscriber packages — triggers auto-registration via __init_subclass__
from ddtrace._trace._registration import FLASK_REQUEST_SUBSCRIBERS
from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
import ddtrace._trace.subscribers.http_client  # noqa: F401
import ddtrace._trace.subscribers.kafka  # noqa: F401
import ddtrace._trace.subscribers.llm  # noqa: F401
import ddtrace._trace.subscribers.messaging  # noqa: F401
import ddtrace._trace.subscribers.molten  # noqa: F401


# Direct integration imports also need listeners when the product lifecycle has not run.
WEB_FRAMEWORK_SUBSCRIBERS.register()
FLASK_REQUEST_SUBSCRIBERS.register()
