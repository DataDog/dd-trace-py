from ddtrace.internal._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    # Importing trace handlers has the side effect of registering integration level
    # handlers. This is necessary to use the Core API in integrations.
    from ddtrace._trace import trace_handlers as _  # noqa: F401
