# The compiled extensions (`_native`, `_threads`, `_msgpack`) are built and shipped by the
# `ddtrace-native` package. `_native` is registered under this historic path before anything
# imports it, so there is a single copy of its state and objects that pickle by their
# `ddtrace.internal.native._native` or `ddtrace._trace.context` names keep resolving
# (`import_module` and the assignment, rather than `import x.y as z`, so the submodule attribute
# is set on this package as a regular import would have done). `_threads` and `_msgpack` bridge
# through their own module files. The Python glue around the extensions lives with its users: the
# post-fork RNG reseed is registered by ddtrace/__init__.py and the on-disk configuration is read by
# settings._core.
from importlib import import_module
import sys


_native = sys.modules[f"{__name__}._native"] = import_module("_ddtrace_native._native")

from ._native import AgentError  # noqa: E402, F401
from ._native import AgentResponse  # noqa: E402, F401
from ._native import BuilderError  # noqa: E402, F401
from ._native import ConfigurationOrigin  # noqa: E402, F401
from ._native import ConnectionFailedError  # noqa: E402, F401
from ._native import DebuggerResponse  # noqa: E402, F401
from ._native import DebuggerSender  # noqa: E402, F401
from ._native import DebuggerSenderError  # noqa: E402, F401
from ._native import DebuggerTrackType  # noqa: E402, F401
from ._native import DeserializationError  # noqa: E402, F401
from ._native import HTTPClient  # noqa: E402, F401
from ._native import HttpClientError  # noqa: E402, F401
from ._native import HttpIoError  # noqa: E402, F401
from ._native import HttpResponse  # noqa: E402, F401
from ._native import InvalidConfigError  # noqa: E402, F401
from ._native import IoError  # noqa: E402, F401
from ._native import LogLevel  # noqa: E402, F401
from ._native import MetricContext  # noqa: E402, F401
from ._native import MetricNamespace  # noqa: E402, F401
from ._native import MetricType  # noqa: E402, F401
from ._native import NetworkError  # noqa: E402, F401
from ._native import PyConfigurator  # noqa: E402, F401
from ._native import PyTracerMetadata  # noqa: E402, F401
from ._native import RemoteConfigCapabilities  # noqa: E402, F401
from ._native import RemoteConfigChange  # noqa: E402, F401
from ._native import RemoteConfigClient  # noqa: E402, F401
from ._native import RemoteConfigProduct  # noqa: E402, F401
from ._native import RemoteConfigReader  # noqa: E402, F401
from ._native import RequestError  # noqa: E402, F401
from ._native import RequestFailedError  # noqa: E402, F401
from ._native import SerializationError  # noqa: E402, F401
from ._native import SharedRuntime  # noqa: E402, F401
from ._native import SymDBSender  # noqa: E402, F401
from ._native import TelemetryWorker  # noqa: E402, F401
from ._native import TimedOutError  # noqa: E402, F401
from ._native import TraceBuffer  # noqa: E402, F401
from ._native import TraceExporter  # noqa: E402, F401
from ._native import TraceExporterBuilder  # noqa: E402, F401
from ._native import config  # noqa: E402, F401
from ._native import generate_128bit_trace_id  # noqa: E402, F401
from ._native import list_distributions  # noqa: E402, F401
from ._native import logger  # noqa: E402, F401
from ._native import process_metrics  # noqa: E402, F401
from ._native import rand64bits  # noqa: E402, F401
from ._native import read_distribution  # noqa: E402, F401
from ._native import scan_distributions  # noqa: E402, F401
from ._native import seed  # noqa: E402, F401
from ._native import store_metadata  # noqa: E402, F401
from ._native import total_memory_bytes  # noqa: E402, F401
from ._native import uuid4  # noqa: E402, F401


try:
    from ._native import DDSketch  # noqa: F401
    from ._native import ffe  # noqa: F401
except ImportError:
    pass
