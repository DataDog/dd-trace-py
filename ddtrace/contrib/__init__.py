# Importing trace handlers has the side effect of registering integration level
# handlers. This is necessary to use the Core API in integrations.
from ddtrace._trace.trace_handlers import common as _  # noqa: F401
from ddtrace._trace.trace_handlers import http_client as __  # noqa: F401
