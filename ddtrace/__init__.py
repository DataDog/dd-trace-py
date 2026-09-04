import sys


LOADED_MODULES = frozenset(sys.modules.keys())


# Ensure we capture references to unpatched modules as early as possible
import ddtrace.internal._unpatched  # noqa
from ._logger import configure_ddtrace_logger  # noqa: E402

# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402

# Enable telemetry writer and excepthook as early as possible to ensure we capture any exceptions from initialization
from ddtrace.internal.runtime import listen_for_identity_refresh_hooks  # noqa: E402,I001
from ddtrace.internal.serverless import in_aws_lambda_microvm  # noqa: E402
import ddtrace.internal.telemetry  # noqa: F401,E402

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal.compat import PYTHON_VERSION_INFO  # noqa: E402
from .internal.settings import env  # noqa: E402
from .internal.settings._config import config  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402
from .internal.utils.deprecations import deprecate  # noqa: E402
from .version import __version__  # noqa: E402


# Register import-time hooks that depend on ddtrace.config being exported.
if in_aws_lambda_microvm():
    from .internal import core as _core  # noqa: E402

    listen_for_identity_refresh_hooks(_core.on)

# TODO: Deprecate accessing tracer from ddtrace.__init__ module in v4.0
if env.get("_DD_GLOBAL_TRACER_INIT", "true").lower() in ("1", "true"):
    from ddtrace.trace import tracer  # noqa: F401

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
    if PYTHON_VERSION_INFO < (3, 10):
        deprecation_message = (
            "Support for ddtrace with Python version %d.%d is deprecated and will be removed in 5.0.0."
        )
        if PYTHON_VERSION_INFO < (3, 9):
            deprecation_message = "Support for ddtrace with Python version %d.%d was removed in 4.0.0."
        deprecate(
            (deprecation_message % (PYTHON_VERSION_INFO[0], PYTHON_VERSION_INFO[1])),
            category=DDTraceDeprecationWarning,
        )


check_supported_python_version()
