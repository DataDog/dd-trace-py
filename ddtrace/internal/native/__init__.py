"""Re-exports of the native extension, which is built and shipped by the ``ddtrace-native`` package.

``ddtrace.internal.native._native`` *is* ``_ddtrace_native._native``: the extension is registered under
the historic path before anything imports it, so there is a single copy of its state and objects that
pickle by their ``ddtrace.internal.native._native`` or ``ddtrace._trace.context`` names keep resolving.
(``import_module`` and the assignment, rather than ``import x.y as z``, so the submodule attribute is set
on this package as a regular import would have done.) ``_native.pyi`` only tells static tools what it exports.
"""

from importlib import import_module
import sys
from typing import Optional

from ddtrace.internal import forksafe as _forksafe
from ddtrace.internal.settings import env


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
from ._native import PyConfigurator  # noqa: E402
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


# Child processes must reseed the RNG after fork. Without a new seed, they use the
# RNG state of the parent and generate the same trace and span IDs. Register the
# hook here, at import time, to set it up as early as possible.
_forksafe.register(seed)


def get_configuration_from_disk() -> tuple[dict[str, str], dict[str, str], dict[str, Optional[str]]]:
    """
    Retrieves the tracer configuration from disk. Calls the PyConfigurator object
    to read the configuration from the disk using the libdatadog shared library
    and returns the corresponding configuration
    See https://github.com/DataDog/libdatadog/blob/06d2b6a19d7ec9f41b3bfd4ddf521585c55298f6/library-config/src/lib.rs
    for more information on how the configuration is read from disk
    """
    debug_logs = env.get("DD_TRACE_DEBUG", "false").lower().strip() in ("true", "1")
    configurator = PyConfigurator(debug_logs)

    # Check if the file override is provided via environment variables
    # This is only used for testing purposes
    local_file_override = env.get("_DD_SC_LOCAL_FILE_OVERRIDE", "")
    managed_file_override = env.get("_DD_SC_MANAGED_FILE_OVERRIDE", "")
    if local_file_override:
        configurator.set_local_file_override(local_file_override)
    if managed_file_override:
        configurator.set_managed_file_override(managed_file_override)

    fleet_config = {}
    fleet_config_ids = {}
    local_config = {}
    try:
        for entry in configurator.get_configuration():
            var_name = entry["name"]
            source = entry["source"]
            if source == "fleet_stable_config":
                fleet_config[var_name] = entry["value"]
                fleet_config_ids[var_name] = entry.get("config_id")
            elif source == "local_stable_config":
                local_config[var_name] = entry["value"]
            else:
                print(f"Unknown configuration source: {source}, for {var_name}")
    except Exception as e:
        # No logger at this point, so we rely on good old print
        print(f"Failed to load configuration from disk, skipping: {e}")
    return fleet_config, local_config, fleet_config_ids
