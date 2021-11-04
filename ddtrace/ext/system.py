"""
Standard system tags
"""
from ddtrace.constants import PID
from ddtrace.internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.ext.system",
    message="Use `ddtrace.constants` module instead",
    version="1.0.0",
)

__all__ = ["PID"]
