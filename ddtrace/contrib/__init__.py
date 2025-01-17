from ddtrace._trace import trace_handlers  # noqa:F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.importlib import func_name  # noqa:F401
from ddtrace.internal.utils.importlib import module_name  # noqa:F401
from ddtrace.internal.utils.importlib import require_modules  # noqa:F401
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("trace_handlers", "func_name", "module_name", "require_modules"):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)
