from ddtrace import config


if config._otel_dd_instrumentation:
    from ddtrace.contrib.compat import core
    import ddtrace.contrib.compat.otel_patcher
else:
    from ddtrace.internal import core

__all__ = ["core"]
