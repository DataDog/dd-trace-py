import sys


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
from .span import Span  # noqa: E402
from .tracer import Tracer  # noqa: E402
from .version import get_version  # noqa: E402


__version__ = get_version()

# a global tracer instance with integration settings
tracer = Tracer()
config._subscribe(["logs_injection", "_trace_sample_rate"], tracer._on_global_config_update)

from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._pubsub import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector


remoteconfig_poller.enable()


class _GlobalConfigPubSub(PubSub):
    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, None)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "GlobalConfig")


remoteconfig_poller.register("APM_TRACING", _GlobalConfigPubSub(callback=config._handle_remoteconfig))

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
