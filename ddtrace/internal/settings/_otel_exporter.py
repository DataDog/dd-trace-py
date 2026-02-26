from __future__ import annotations

import os
from typing import Optional


def _get_env_non_empty(key: str) -> Optional[str]:
    """Return stripped env value if set and non-empty, else None."""
    val = os.environ.get(key)
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _parse_otel_headers(headers_str: Optional[str]) -> dict[str, str]:
    """Parse OTEL header string (key1=value1,key2=value2) into a dict."""
    out: dict[str, str] = {}
    if not headers_str or not headers_str.strip():
        return out
    for part in headers_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, _, val = part.partition("=")
            key, val = key.strip(), val.strip()
            if key:
                out[key] = val
        else:
            out[part] = ""
    return out


# Default OTLP endpoints
OTLP_HTTP_DEFAULT_ENDPOINT = "http://localhost:4318"
OTLP_HTTP_TRACES_PATH = "/v1/traces"
OTLP_GRPC_DEFAULT_ENDPOINT = "http://localhost:4317"  # grpc support not implemented yet
# Supported protocol (as of now)
OTLP_PROTOCOL_HTTP_JSON = "http/json"
# Default timeout: 10000 ms per OTEL convention (e.g. ddtrace opentelemetry DEFAULT_TIMEOUT)
OTLP_TIMEOUT_MS_DEFAULT = 10000


def _get_otel_traces_config(key: str, generic_key: str, default: Optional[str] = None) -> Optional[str]:
    """Read config with traces-specific override: TRACES_* over generic OTEL_*."""
    return _get_env_non_empty(key) or _get_env_non_empty(generic_key) or default


def _get_otel_traces_protocol() -> str:
    """Protocol for OTLP trace export. Default http/json for first version."""
    val = _get_otel_traces_config(
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        default=OTLP_PROTOCOL_HTTP_JSON,
    )
    return (val or OTLP_PROTOCOL_HTTP_JSON).lower()


def _get_otel_traces_endpoint() -> Optional[str]:
    """Endpoint URL for OTLP traces (traces-specific over generic)."""
    return _get_otel_traces_config(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    )


def _get_otel_traces_headers() -> dict[str, str]:
    """HTTP headers for OTLP trace export (key1=value1,key2=value2)."""
    val = _get_otel_traces_config(
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_HEADERS",
    )
    if not val:
        return {}
    return _parse_otel_headers(val)


def _get_otel_traces_timeout_seconds() -> float:
    """Request timeout in seconds. OTEL env vars are in milliseconds."""
    val = _get_otel_traces_config(
        "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT",
        "OTEL_EXPORTER_OTLP_TIMEOUT",
    )
    if not val:
        return OTLP_TIMEOUT_MS_DEFAULT / 1000.0
    try:
        ms = float(val)
        if ms > 0:
            return ms / 1000.0
    except (TypeError, ValueError):
        pass
    return OTLP_TIMEOUT_MS_DEFAULT / 1000.0


def _resolve_otlp_traces_url() -> str:
    """Resolve full OTLP traces URL (endpoint + path for HTTP).

    When OTEL_EXPORTER_OTLP_TRACES_ENDPOINT (or generic) is set, use it as-is.
    When no endpoint is set, use HTTP default with /v1/traces (only HTTP/JSON
    is supported).
    """
    endpoint = _get_otel_traces_endpoint()
    if endpoint:
        return endpoint.rstrip("/")
    # No endpoint set: use HTTP default with /v1/traces
    return OTLP_HTTP_DEFAULT_ENDPOINT.rstrip("/") + OTLP_HTTP_TRACES_PATH


def _is_otlp_traces_endpoint_set() -> bool:
    """True when user has set at least one of the OTLP endpoint env vars."""
    return any(
        _get_env_non_empty(k) is not None for k in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")
    )


def _is_otlp_traces_exporter_otlp() -> bool:
    """True when OTEL_TRACES_EXPORTER is set to otlp (use OTLP trace export)."""
    return (_get_env_non_empty("OTEL_TRACES_EXPORTER") or "").lower() == "otlp"


class OTLPTraceExporterConfig:
    """
    Configuration for OTLP trace export.

    Enablement (Option A): OTLP is enabled when OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
    or OTEL_EXPORTER_OTLP_ENDPOINT is set. Single export: either Datadog agent or OTLP.
    """

    @property
    def otlp_traces_enabled(self) -> bool:
        """True when OTLP trace export should be used: endpoint set or OTEL_TRACES_EXPORTER=otlp."""
        return _is_otlp_traces_endpoint_set() or _is_otlp_traces_exporter_otlp()

    @property
    def otlp_traces_endpoint(self) -> str:
        """Full URL for OTLP trace export (e.g. http://localhost:4318/v1/traces)."""
        return _resolve_otlp_traces_url()

    @property
    def otlp_traces_headers(self) -> dict[str, str]:
        """HTTP headers to send with OTLP trace requests."""
        return _get_otel_traces_headers()

    @property
    def otlp_traces_timeout_seconds(self) -> float:
        """Request timeout in seconds for OTLP trace export."""
        return _get_otel_traces_timeout_seconds()

    @property
    def otlp_traces_protocol(self) -> str:
        """Protocol from env (e.g. http/json). This version supports http/json only."""
        return _get_otel_traces_protocol()


config = OTLPTraceExporterConfig()
