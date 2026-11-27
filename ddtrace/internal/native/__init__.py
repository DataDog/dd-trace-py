from ddtrace.internal import forksafe as _forksafe

from ._native import AgentError  # noqa: F401
from ._native import AgentResponse  # noqa: F401
from ._native import BuilderError  # noqa: F401
from ._native import ConfigurationOrigin  # noqa: F401
from ._native import ConnectionFailedError  # noqa: F401
from ._native import DebuggerResponse  # noqa: F401
from ._native import DebuggerSender  # noqa: F401
from ._native import DebuggerSenderError  # noqa: F401
from ._native import DebuggerTrackType  # noqa: F401
from ._native import DeserializationError  # noqa: F401
from ._native import HTTPClient  # noqa: F401
from ._native import HttpClientError  # noqa: F401
from ._native import HttpIoError  # noqa: F401
from ._native import HttpResponse  # noqa: F401
from ._native import InvalidConfigError  # noqa: F401
from ._native import IoError  # noqa: F401
from ._native import LogLevel  # noqa: F401
from ._native import MetricContext  # noqa: F401
from ._native import MetricNamespace  # noqa: F401
from ._native import MetricType  # noqa: F401
from ._native import NetworkError  # noqa: F401
from ._native import PyConfigurator  # noqa: F401
from ._native import PyTracerMetadata  # noqa: F401
from ._native import RemoteConfigCapabilities  # noqa: F401
from ._native import RemoteConfigChange  # noqa: F401
from ._native import RemoteConfigClient  # noqa: F401
from ._native import RemoteConfigProduct  # noqa: F401
from ._native import RemoteConfigReader  # noqa: F401
from ._native import RequestError  # noqa: F401
from ._native import RequestFailedError  # noqa: F401
from ._native import SerializationError  # noqa: F401
from ._native import SharedRuntime  # noqa: F401
from ._native import SymDBSender  # noqa: F401
from ._native import TelemetryWorker  # noqa: F401
from ._native import TimedOutError  # noqa: F401
from ._native import TraceBuffer  # noqa: F401
from ._native import TraceExporter  # noqa: F401
from ._native import TraceExporterBuilder  # noqa: F401
from ._native import config  # noqa: F401
from ._native import generate_128bit_trace_id  # noqa: F401
from ._native import list_distributions  # noqa: F401
from ._native import logger  # noqa: F401
from ._native import process_metrics  # noqa: F401
from ._native import rand64bits  # noqa: F401
from ._native import read_distribution  # noqa: F401
from ._native import scan_distributions  # noqa: F401
from ._native import seed  # noqa: F401
from ._native import store_metadata  # noqa: F401
from ._native import total_memory_bytes  # noqa: F401
from ._native import uuid4  # noqa: F401


try:
    from ._native import DDSketch  # noqa: F401
    from ._native import ffe  # noqa: F401
except ImportError:
    pass


# Child processes must reseed the RNG after fork. Without a new seed, they use the
# RNG state of the parent and generate the same trace and span IDs. Register the
# hook here, at import time, to set it up as early as possible.
_forksafe.register(seed)
