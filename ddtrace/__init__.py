import sys
import warnings

LOADED_MODULES = frozenset(sys.modules.keys())

from ddtrace.internal.module import ModuleWatchdog

ModuleWatchdog.install()

# Acquire a reference to the threading module. Some parts of the library (e.g.
# the profiler) might be enabled programmatically and therefore might end up
# getting a reference to the tracee's threading module. By storing a reference
# to the threading module used by ddtrace here, we make it easy for those parts
# to get a reference to the right threading module.
import threading as _threading

from ._logger import configure_ddtrace_logger


# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402

from .settings import _config as config

if config._telemetry_enabled:
    from ddtrace.internal import telemetry

    telemetry.install_excepthook()
    # In order to support 3.12, we start the writer upon initialization.
    # See https://github.com/python/cpython/pull/104826.
    # Telemetry events will only be sent after the `app-started` is queued.
    # This will occur when the agent writer starts.
    telemetry.telemetry_writer.enable()

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402
from .pin import Pin  # noqa: E402
from .settings import _config as config  # noqa: E402
from ddtrace._trace.span import Span  # noqa: E402
from ddtrace._trace.tracer import Tracer  # noqa: E402
from ddtrace.vendor import debtcollector
from .version import get_version  # noqa: E402

# DEV: Import deprecated tracer module in order to retain side-effect of package
# initialization, which added this module to sys.modules. We catch deprecation
# warnings as this is only to retain a side effect of the package
# initialization.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from .tracer import Tracer as _


__version__ = get_version()

# a global tracer instance with integration settings
tracer = Tracer()

__all__ = [
    "patch",
    "patch_all",
    "Pin",
    "Span",
    "tracer",
    "Tracer",
    "config",
    "DDTraceDeprecationWarning",
]


_DEPRECATED_MODULE_ATTRIBUTES = [
    "Span",
    "Tracer",
]


def __getattr__(name):
    if name in _DEPRECATED_MODULE_ATTRIBUTES:
        debtcollector.deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            category=DDTraceDeprecationWarning,
        )

    if name in globals():
        return globals()[name]

    raise AttributeError("%s has no attribute %s", __name__, name)
