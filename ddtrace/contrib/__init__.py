# Importing trace handlers has the side effect of registering integration level
# handlers. This is necessary to use the Core API in integrations.
from ddtrace._trace import trace_handlers as _  # noqa: F401

# Import subscriber packages — triggers auto-registration via __init_subclass__
import ddtrace._trace.subscribers  # noqa: F401
