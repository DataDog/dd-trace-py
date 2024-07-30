from ddtrace.vendor.debtcollector import deprecate

from ..internal.aiopg.patch import *  # noqa: F401,F403


deprecate(
    "This module is deprecated and will be removed in a future version. Avoid importing from this module.",
    version="3.0.0",
)
