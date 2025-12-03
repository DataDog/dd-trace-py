# Importing trace handlers has the side effect of registering integration level
# handlers. This is necessary to use the Core API in integrations.
from ddtrace import config


if not config._otel_dd_instrumentation:
    from ddtrace._trace import trace_handlers as _  # noqa: F401
else:
    from ddtrace.contrib.compat import otel_trace_handlers as _  # noqa: F401
