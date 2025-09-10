"""
Core stubs that don't have circular import dependencies.
These are the basic stubs needed early in the import process.
"""

from typing import Dict
from typing import List

from ._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    import logging

    import wrapt
    from wrapt.importer import when_imported
else:
    # Provide minimal stubs when instrumentation is disabled

    class logging:  # type: ignore[no-redef]
        DEBUG = 10
        INFO = 20
        WARNING = 30
        ERROR = 40
        CRITICAL = 50

        class Logger:
            def debug(self, *args, **kwargs):
                pass

            def info(self, *args, **kwargs):
                pass

            def warning(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

            def critical(self, *args, **kwargs):
                pass

            def setLevel(self, level):
                pass

            def addHandler(self, handler):
                pass

            def addFilter(self, _filter):
                pass

            def isEnabledFor(self, level):
                return False

            handlers: List = []

        class LogRecord:
            def __init__(self, *args, **kwargs):
                self.name = ""
                self.msg = ""
                self.pathname = ""
                self.lineno = 0
                self.levelno = 0

        @staticmethod
        def getLogger(name):
            return logging.Logger()

        class StreamHandler:
            def __init__(self):
                pass

            def setLevel(self, level):
                pass

            def setFormatter(self, formatter):
                pass

            def set_name(self, name):
                pass

        class Formatter:
            def __init__(self, fmt):
                pass

        @staticmethod
        def warning(msg, *args):
            pass

    class wrapt:  # type: ignore[no-redef]
        class ObjectProxy:
            def __init__(self, wrapped):
                self._self_wrapped = wrapped

            def __getattr__(self, name):
                return getattr(self._self_wrapped, name)

        @staticmethod
        def decorator(wrapper):
            def _decorator(wrapped):
                return wrapped

            return _decorator

        class importer:
            @staticmethod
            def when_imported(name):
                def decorator(func):
                    return func

                return decorator

    def when_imported(x):
        return lambda y: None


# Configuration stubs - comprehensive version that implements ConfigProtocol
class _NullConfig:
    # Implement all the Protocol attributes with sensible defaults
    _data_streams_enabled: bool = False
    _from_endpoint: Dict = {}
    _remote_config_enabled: bool = False
    _sca_enabled: bool = False
    _trace_safe_instrumentation_enabled: bool = False
    _modules_to_report = None
    _report_handled_errors = None
    _configured_modules = None
    _instrument_user_code: bool = False
    _instrument_third_party_code: bool = False
    _instrument_all: bool = False
    _stacktrace_resolver = None
    _enabled: bool = False
    _http_tag_query_string: bool = False
    _health_metrics_enabled: bool = False
    _trace_writer_interval_seconds: float = 1.0
    _trace_writer_connection_reuse: bool = False
    _trace_writer_log_err_payload: bool = False
    _trace_api: str = "v0.5"
    _trace_writer_buffer_size: int = 1000
    _trace_writer_payload_size: int = 1000
    _http = None
    _logs_injection: bool = False
    _dd_site = None
    _dd_api_key = None
    _llmobs_ml_app = None
    _llmobs_instrumented_proxy_urls = None
    _llmobs_agentless_enabled = False
    _trace_compute_stats: bool = False
    _tracing_enabled: bool = False

    # Common properties
    service = None
    env = None
    version = None
    tags: Dict = {}
    service_mapping: Dict = {}

    def __getattr__(self, name):
        # Handle dynamic attributes and integration configs
        if name.endswith("_enabled"):
            return False
        if name.endswith("_timeout"):
            return 0
        if name.startswith("_") and "interval" in name:
            return 1.0
        if name.startswith("_") and ("size" in name or "limit" in name):
            return 1000
        return False

    def _add_extra_service(self, service_name: str) -> None:
        pass  # No-op for null config

    def _get_extra_services(self):
        return set()


# Function to get the appropriate config instance based on instrumentation status
def get_config():
    """Get the appropriate config instance - real Config when instrumentation enabled, _NullConfig otherwise."""
    if _INSTRUMENTATION_ENABLED:
        # Import the real config when instrumentation is enabled (lazy import to avoid circular imports)
        from ddtrace.settings._config import config

        return config
    else:
        # Use the null config when instrumentation is disabled
        return _NullConfig()


# Export the core stubs
__all__ = ["logging", "wrapt", "_NullConfig", "when_imported", "get_config"]
