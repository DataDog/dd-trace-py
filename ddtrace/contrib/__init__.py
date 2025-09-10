# Importing trace handlers has the side effect of registering integration level
# handlers. This is necessary to use the Core API in integrations.
#
# Should not be called when _INSTRUMENTATION_ENABLED is False
from ddtrace._trace import trace_handlers as _  # noqa: F401
