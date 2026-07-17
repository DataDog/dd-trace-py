"""Shared, dependency-free holder for configuration facts.

ddtrace.internal.settings._config computes global tracer configuration.
Downstream packages such as ddtrace.internal.writer.writer need many of those
facts, but importing _config from there would recreate circular imports
(_config depends on telemetry and other packages that eventually depend on
the writer stack).

This module lives directly under ddtrace.internal (rather than
ddtrace.internal.settings) and has no dependencies of its own, so it can sit
underneath both without closing import loops: _config.py sets the values once
during Config construction (and on subsequent updates), and consumers read
them directly.

Values that are read directly from environment variables are not stored here;
see ddtrace.internal.settings.env.
"""

from typing import Optional


_trace_api: Optional[str] = None
_trace_writer_buffer_size: Optional[int] = None
_trace_writer_connection_reuse: Optional[bool] = None
_health_metrics_enabled: Optional[bool] = None
_report_hostname: Optional[bool] = None
_service: Optional[str] = None
_env: Optional[str] = None
_version: Optional[str] = None
_debug_mode: Optional[bool] = None
_dd_site: Optional[str] = None
_dd_api_key: Optional[str] = None
_otel_metrics_enabled: Optional[bool] = None
_trace_compute_stats: Optional[bool] = None
_llmobs_enabled: Optional[bool] = None


def set_trace_api(value: Optional[str]) -> None:
    global _trace_api
    _trace_api = value


def trace_api() -> Optional[str]:
    return _trace_api


def set_trace_writer_buffer_size(value: int) -> None:
    global _trace_writer_buffer_size
    _trace_writer_buffer_size = value


def trace_writer_buffer_size() -> int:
    return _trace_writer_buffer_size  # type: ignore[return-value]


def set_trace_writer_connection_reuse(value: bool) -> None:
    global _trace_writer_connection_reuse
    _trace_writer_connection_reuse = value


def trace_writer_connection_reuse() -> bool:
    return bool(_trace_writer_connection_reuse)


def set_health_metrics_enabled(value: bool) -> None:
    global _health_metrics_enabled
    _health_metrics_enabled = value


def health_metrics_enabled() -> bool:
    return bool(_health_metrics_enabled)


def set_report_hostname(value: bool) -> None:
    global _report_hostname
    _report_hostname = value


def report_hostname() -> bool:
    return bool(_report_hostname)


def set_service(value: Optional[str]) -> None:
    global _service
    _service = value


def service() -> Optional[str]:
    return _service


def set_env(value: Optional[str]) -> None:
    global _env
    _env = value


def env() -> Optional[str]:
    return _env


def set_version(value: Optional[str]) -> None:
    global _version
    _version = value


def version() -> Optional[str]:
    return _version


def set_debug_mode(value: bool) -> None:
    global _debug_mode
    _debug_mode = value


def debug_mode() -> bool:
    return bool(_debug_mode)


def set_dd_site(value: str) -> None:
    global _dd_site
    _dd_site = value


def dd_site() -> str:
    return _dd_site or ""


def set_dd_api_key(value: Optional[str]) -> None:
    global _dd_api_key
    _dd_api_key = value


def dd_api_key() -> Optional[str]:
    return _dd_api_key


def set_otel_metrics_enabled(value: bool) -> None:
    global _otel_metrics_enabled
    _otel_metrics_enabled = value


def otel_metrics_enabled() -> bool:
    return bool(_otel_metrics_enabled)


def set_trace_compute_stats(value: bool) -> None:
    global _trace_compute_stats
    _trace_compute_stats = value


def trace_compute_stats() -> bool:
    return bool(_trace_compute_stats)


def set_llmobs_enabled(value: bool) -> None:
    global _llmobs_enabled
    _llmobs_enabled = value


def llmobs_enabled() -> bool:
    return bool(_llmobs_enabled)
