"""
Standard system tags
"""
from ddtrace.constants import PID

from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.ext.system",
    replacement="ddtrace.constants",
    removal_version="1.0.0",
)

__all__ = ["PID"]
