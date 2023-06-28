import sys


LOADED_MODULES = frozenset(sys.modules.keys())

# Acquire a reference to the threading module. Some parts of the library (e.g.
# the profiler) might be enabled programmatically and therefore might end up
# getting a reference to the tracee's threading module. By storing a reference
# to the threading module used by ddtrace here, we make it easy for those parts
# to get a reference to the right threading module.
import threading as _threading

from ddtrace.internal.module import ModuleWatchdog

from ._logger import configure_ddtrace_logger


# ModuleWatchdog.install()


# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402


from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402
from .pin import Pin  # noqa: E402
from .version import get_version  # noqa: E402


__version__ = get_version()

from ._config import _default_config
from .settings.config import Config  # noqa: E402


config = Config(*_default_config())

# Depends on the global config

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal import telemetry  # noqa: E402
from .internal.remoteconfig import RemoteConfig  # noqa: E402
from .span import Span  # noqa: E402
from .tracer import Tracer  # noqa: E402


RemoteConfig.enable()
RemoteConfig.register("APM_TRACING", config._update_rc)
telemetry.install_excepthook()


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
