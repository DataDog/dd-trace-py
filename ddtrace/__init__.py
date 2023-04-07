import sys


def global_excepthook(tp, value, traceback):
    """The global tracer except hook."""
    if asbool(os.getenv("DD_INSTRUMENTATION_TELEMETRY_ENABLED", True)):
        telemetry_writer.add_error(1, repr(value))
        telemetry_writer.enable()


_ORIGINAL_EXCEPTHOOK = sys.excepthook


def _excepthook(tp, value, traceback):
    global_excepthook(tp, value, traceback)
    return _ORIGINAL_EXCEPTHOOK(tp, value, traceback)


def install_excepthook():
    """Install a hook that intercepts unhandled exception and send metrics about them."""
    sys.excepthook = _excepthook


def uninstall_excepthook():
    """Uninstall the global tracer except hook."""
    sys.excepthook = _ORIGINAL_EXCEPTHOOK


install_excepthook()
import os

from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.utils.formats import asbool


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
from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402
from .pin import Pin  # noqa: E402
from .settings import _config as config  # noqa: E402
from .span import Span  # noqa: E402
from .tracer import Tracer  # noqa: E402
from .version import get_version  # noqa: E402


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
