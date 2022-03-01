"""
Standard system tags
"""
from ddtrace.constants import PID
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.ext.system",
    replacement="ddtrace.constants",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)

__all__ = ["PID"]
