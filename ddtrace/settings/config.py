from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.settings._config import *  # noqa: F403
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.settings.config module is deprecated",
    message="Access the global configuration using ``ddtrace.config``.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
