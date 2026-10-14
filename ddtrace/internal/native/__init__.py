from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any


if TYPE_CHECKING:
    from ._native import AgentError  # noqa: F401
    from ._native import AgentResponse  # noqa: F401
    from ._native import BuilderError  # noqa: F401
    from ._native import ConfigurationOrigin  # noqa: F401
    from ._native import ConnectionFailedError  # noqa: F401
    from ._native import DDSketch  # noqa: F401
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
    from ._native import TraceExporter  # noqa: F401
    from ._native import TraceExporterBuilder  # noqa: F401
    from ._native import config  # noqa: F401
    from ._native import ffe  # noqa: F401
    from ._native import generate_128bit_trace_id  # noqa: F401
    from ._native import logger  # noqa: F401
    from ._native import process_metrics  # noqa: F401
    from ._native import rand64bits  # noqa: F401
    from ._native import scan_distributions  # noqa: F401
    from ._native import seed  # noqa: F401
    from ._native import stable_configuration_paths  # noqa: F401
    from ._native import store_metadata  # noqa: F401
    from ._native import total_memory_bytes  # noqa: F401


# Re-exports from the compiled extension are resolved lazily via module-level
# __getattr__ (PEP 562): importing this package does not load the shared library,
# only accessing one of the names below does.
_NATIVE_EXPORTS = frozenset(
    {
        "AgentError",
        "AgentResponse",
        "BuilderError",
        "ConfigurationOrigin",
        "ConnectionFailedError",
        "DDSketch",
        "DebuggerResponse",
        "DebuggerSender",
        "DebuggerSenderError",
        "DebuggerTrackType",
        "DeserializationError",
        "HTTPClient",
        "HttpClientError",
        "HttpIoError",
        "HttpResponse",
        "InvalidConfigError",
        "IoError",
        "LogLevel",
        "MetricContext",
        "MetricNamespace",
        "MetricType",
        "NetworkError",
        "PyTracerMetadata",
        "RemoteConfigCapabilities",
        "RemoteConfigChange",
        "RemoteConfigClient",
        "RemoteConfigProduct",
        "RemoteConfigReader",
        "RequestError",
        "RequestFailedError",
        "SerializationError",
        "SharedRuntime",
        "SymDBSender",
        "TelemetryWorker",
        "TimedOutError",
        "TraceExporter",
        "TraceExporterBuilder",
        "config",
        "ffe",
        "generate_128bit_trace_id",
        "logger",
        "process_metrics",
        "rand64bits",
        "scan_distributions",
        "seed",
        "stable_configuration_paths",
        "store_metadata",
        "total_memory_bytes",
    }
)


def __getattr__(name: str) -> Any:
    if name in _NATIVE_EXPORTS:
        from . import _native

        _run_native_load_callbacks()
        return getattr(_native, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Deferred initialization: pure-Python code that must configure the native
# library before first use registers callbacks here instead of importing the
# extension eagerly. Callbacks run once, immediately after `_native` first
# loads, and are dropped if the extension never loads.
_native_load_callbacks: list[Callable[[], None]] = []
_native_load_callbacks_ran = False


def _run_native_load_callbacks() -> None:
    global _native_load_callbacks_ran
    if _native_load_callbacks_ran:
        return
    _native_load_callbacks_ran = True
    callbacks = _native_load_callbacks[:]
    _native_load_callbacks.clear()
    for callback in callbacks:
        callback()


def on_native_load(callback: Callable[[], None]) -> None:
    """Register a callback to run when the native extension first loads (immediately if it already has)."""
    if _native_load_callbacks_ran:
        callback()
    else:
        _native_load_callbacks.append(callback)


# Stable-configuration discovery lives in the base layer; re-exported here for compatibility.
from ddtrace_base.settings._stable_config import _FLEET_STABLE_CONFIGURATION_PATH  # noqa: E402,F401
from ddtrace_base.settings._stable_config import _LOCAL_STABLE_CONFIGURATION_PATH  # noqa: E402,F401
from ddtrace_base.settings._stable_config import _stable_config_paths  # noqa: E402,F401
from ddtrace_base.settings._stable_config import get_configuration_from_disk  # noqa: E402,F401
