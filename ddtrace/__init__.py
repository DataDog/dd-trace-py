import sys
import os
import warnings


LOADED_MODULES = frozenset(sys.modules.keys())

# Configuration for the whole tracer from file. Do it before anything else happens.
from ddtrace.internal.native import _apply_configuration_from_disk

_apply_configuration_from_disk()

from ddtrace.internal.module import ModuleWatchdog


ModuleWatchdog.install()

# Ensure we capture references to unpatched modules as early as possible
import ddtrace.internal._unpatched  # noqa
from ._logger import configure_ddtrace_logger

# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402

from .settings import _global_config as config


# Enable telemetry writer and excepthook as early as possible to ensure we capture any exceptions from initialization
import ddtrace.internal.telemetry  # noqa: E402

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal.compat import PYTHON_VERSION_INFO  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402

# TODO(munir): Remove the imports below in v3.0
from ddtrace._trace import pin as _p  # noqa: E402, F401
from ddtrace._trace import span as _s  # noqa: E402, F401
from ddtrace._trace import tracer as _t  # noqa: E402, F401
from ddtrace.vendor import debtcollector
from .version import get_version  # noqa: E402


# TODO(mabdinur): Remove this once we have a better way to start the mini agent
from ddtrace.internal.serverless.mini_agent import maybe_start_serverless_mini_agent as _start_mini_agent

_start_mini_agent()

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
