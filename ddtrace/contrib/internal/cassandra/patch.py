from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from .session import patch  # noqa: F401
from .session import unpatch  # noqa: F401
