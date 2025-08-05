import sys
import os


LOADED_MODULES = frozenset(sys.modules.keys())


# Ensure we capture references to unpatched modules as early as possible
import ddtrace.internal._unpatched  # noqa
from ._logger import configure_ddtrace_logger

# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402

from .settings._config import config


# Enable telemetry writer and excepthook as early as possible to ensure we capture any exceptions from initialization
import ddtrace.internal.telemetry  # noqa: E402

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal.compat import PYTHON_VERSION_INFO  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402

from ddtrace.vendor import debtcollector
from .version import get_version  # noqa: E402

__version__ = get_version()

# TODO: Deprecate accessing tracer from ddtrace.__init__ module in v4.0
if os.environ.get("_DD_GLOBAL_TRACER_INIT", "true").lower() in ("1", "true"):
    from ddtrace.trace import tracer  # noqa: F401

__all__ = [
    "patch",
    "patch_all",
    "config",
    "DDTraceDeprecationWarning",
]


def check_supported_python_version():
    if PYTHON_VERSION_INFO < (3, 8):
        deprecation_message = (
            "Support for ddtrace with Python version %d.%d is deprecated and will be removed in 3.0.0."
        )
        if PYTHON_VERSION_INFO < (3, 7):
            deprecation_message = "Support for ddtrace with Python version %d.%d was removed in 2.0.0."
        debtcollector.deprecate(
            (deprecation_message % (PYTHON_VERSION_INFO[0], PYTHON_VERSION_INFO[1])),
            category=DDTraceDeprecationWarning,
        )


check_supported_python_version()
