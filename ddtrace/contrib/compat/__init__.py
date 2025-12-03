from ddtrace import config


if config._otel_dd_instrumentation:
    from ddtrace.contrib.compat import core  # Use OTel-compatible core
else:
    from ddtrace.internal import core  # Use normal DD core

__all__ = ["core"]
