"""This module is used to ensure integrations can
work with ddtrace and OTel SDK
"""

from ddtrace import config


if config._otel_dd_instrumentation:
    from .otel import core
    from .otel import span_patcher  # noqa: F401
else:
    from ddtrace.internal import core

__all__ = ["core"]
