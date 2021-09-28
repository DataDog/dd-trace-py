"""
Standard system tags
"""
from ddtrace.constants import PID
from ddtrace.utils.deprecation import deprecation


deprecation(
    name="ddtrace.ext.system",
    message="Use `ddtrace.constant` package instead",
    version="1.0.0",
)

__all__ = ["PID"]
