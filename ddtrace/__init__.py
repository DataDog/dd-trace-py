import sys


LOADED_MODULES = frozenset(sys.modules.keys())


# Ensure we capture references to unpatched modules as early as possible
import ddtrace.internal._unpatched  # noqa
from ._logger import configure_ddtrace_logger  # noqa: E402

# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402

# Enable telemetry writer and excepthook as early as possible to ensure we capture any exceptions from initialization
import ddtrace.internal.telemetry  # noqa: E402, F401

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal.compat import PYTHON_VERSION_INFO  # noqa: E402
from .internal.compat import is_at_least_py  # noqa: E402
from .internal.settings import env  # noqa: E402
from .internal.settings._config import config  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402
from .internal.utils.deprecations import deprecate  # noqa: E402
from .version import __version__  # noqa: E402


def _on_trace_module_imported(trace_module) -> None:
    """Publish the global tracer's active context to the OpenTelemetry thread context.

    Wired here rather than in ddtrace.trace so that the tracing product does not depend on the OpenTelemetry one.
    Imported lazily so that skipping global tracer creation does not load the native extension.
    """
    from ddtrace.internal.opentelemetry.thread_context import register_otel_thread_context_listener

    register_otel_thread_context_listener(trace_module.tracer)


# TODO: Deprecate accessing tracer from ddtrace.__init__ module in v4.0
if env.get("_DD_GLOBAL_TRACER_INIT", "true").lower() in ("1", "true"):
    from ddtrace.trace import tracer  # noqa: E402, F401

    _on_trace_module_imported(sys.modules["ddtrace.trace"])
else:
    from ddtrace.internal.module import ModuleWatchdog  # noqa: E402

    # An explicit import of ddtrace.trace still creates the global tracer.
    ModuleWatchdog.register_module_hook("ddtrace.trace", _on_trace_module_imported)

# Initialize DSM support and register DSM handlers (if enabled)
import ddtrace.internal.datastreams as _  # noqa: E402, F401


__all__ = [
    "__version__",
    "patch",
    "patch_all",
    "config",
    "DDTraceDeprecationWarning",
]


def check_supported_python_version():
    if not is_at_least_py(3, 10):
        deprecation_message = (
            "Support for ddtrace with Python version %d.%d is deprecated and will be removed in 5.0.0."
        )
        if not is_at_least_py(3, 9):
            deprecation_message = "Support for ddtrace with Python version %d.%d was removed in 4.0.0."
        deprecate(
            (deprecation_message % (PYTHON_VERSION_INFO[0], PYTHON_VERSION_INFO[1])),
            category=DDTraceDeprecationWarning,
        )


check_supported_python_version()
