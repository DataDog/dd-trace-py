"""Configuration Protocol definition for type checking."""

from typing import Any
from typing import Dict
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class ConfigProtocol(Protocol):
    """Protocol defining the interface for config objects.

    This includes all commonly accessed attributes across the codebase.
    The __getattr__ method handles dynamic attributes and integration configs.
    """

    # Core attributes used throughout the codebase
    _data_streams_enabled: bool
    _from_endpoint: Dict[str, Any]
    _remote_config_enabled: bool
    _sca_enabled: bool
    _trace_safe_instrumentation_enabled: bool
    _modules_to_report: Any
    _report_handled_errors: Any
    _configured_modules: Any
    _instrument_user_code: bool
    _instrument_third_party_code: bool
    _instrument_all: bool
    _stacktrace_resolver: Any
    _enabled: bool
    _http_tag_query_string: bool
    _health_metrics_enabled: bool
    _trace_writer_interval_seconds: float
    _trace_writer_connection_reuse: bool
    _trace_writer_log_err_payload: bool
    _trace_api: str
    _trace_writer_buffer_size: int
    _trace_writer_payload_size: int
    _http: Any
    _logs_injection: bool
    _dd_site: Any
    _dd_api_key: Any
    _llmobs_ml_app: Any
    _llmobs_instrumented_proxy_urls: Any
    _llmobs_agentless_enabled: Any
    _trace_compute_stats: bool
    _tracing_enabled: bool

    # Common properties
    service: Any
    env: Any
    version: Any
    tags: Dict[str, Any]
    service_mapping: Dict[str, str]

    # Methods
    def __getattr__(self, name: str) -> Any:
        ...

    def _add_extra_service(self, service_name: str) -> None:
        ...

    def _get_extra_services(self) -> Any:
        ...
