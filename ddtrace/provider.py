from ddtrace._trace.provider import BaseContextProvider  # noqa: F401
from ddtrace._trace.provider import DatadogContextMixin  # noqa: F401
from ddtrace._trace.provider import DefaultContextProvider  # noqa: F401
from ddtrace.internal.ci_visibility.context import CIContextProvider  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The context provider interface is deprecated",
    message="Import BaseContextProvider from `ddtrace.trace` instead.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
