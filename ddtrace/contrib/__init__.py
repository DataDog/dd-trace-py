# Importing trace handlers has the side effect of registering integration level
# handlers. This is necessary to use the Core API in integrations.
from ddtrace import config
from ddtrace._trace import trace_handlers as _  # noqa: F401


if config._otel_dd_instrumentation:
    from ddtrace.contrib.compat.otel import trace_handlers  # noqa: F401
