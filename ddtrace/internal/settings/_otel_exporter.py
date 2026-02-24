"""
OTLP trace exporter configuration.

Supports standard OTEL exporter environment variables for traces with
traces-specific keys taking precedence over generic OTEL_EXPORTER_OTLP_*.

Enablement (Option A): OTLP trace export is used when the user sets at least
one of OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or OTEL_EXPORTER_OTLP_ENDPOINT.
If neither is set, the tracer uses Datadog agent export only (single export).
"""

from __future__ import annotations

import os
from typing import Optional

from ddtrace.internal.utils.formats import parse_tags_str


# Default OTLP endpoints per protocol (HTTP/JSON and HTTP/protobuf use same port)
OTLP_HTTP_DEFAULT_ENDPOINT = "http://localhost:4318"
OTLP_HTTP_TRACES_PATH = "/v1/traces"
OTLP_GRPC_DEFAULT_ENDPOINT = "http://localhost:4317"

# Supported protocol for first version
OTLP_PROTOCOL_HTTP_JSON = "http/json"


def _get_otel_traces_config(key: str, generic_key: str, default: Optional[str] = None) -> Optional[str]:
    """Read config with traces-specific override: TRACES_* over generic OTEL_*."""
    val = os.environ.get(key)
    if val is not None and str(val).strip() != "":
        return str(val).strip()
    val = os.environ.get(generic_key)
    if val is not None and str(val).strip() != "":
        return str(val).strip()
    return default


def _get_otel_traces_protocol() -> str:
    """Protocol for OTLP trace export. Default http/json for first version."""
    val = _get_otel_traces_config(
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        default=OTLP_PROTOCOL_HTTP_JSON,
    )
    if val is None:
        return OTLP_PROTOCOL_HTTP_JSON
    return val.strip().lower()


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
    return parse_tags_str(val)


def _get_otel_traces_timeout_seconds() -> float:
    """Request timeout in seconds. OTEL convention often uses milliseconds."""
    val = _get_otel_traces_config(
        "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT",
        "OTEL_EXPORTER_OTLP_TIMEOUT",
    )
    if not val:
        return 10.0
    try:
        n = float(val)
        # If value is small (< 100), assume seconds; else assume milliseconds
        if n > 0 and n < 100:
            return n
        if n >= 100:
            return n / 1000.0
    except (TypeError, ValueError):
        pass
    return 10.0


def _resolve_otlp_traces_url() -> str:
    """Resolve full OTLP traces URL (endpoint + path for HTTP)."""
    endpoint = _get_otel_traces_endpoint()
    protocol = _get_otel_traces_protocol()
    if endpoint:
        endpoint = endpoint.rstrip("/")
        if protocol in ("http/json", "http/protobuf") and not endpoint.endswith(OTLP_HTTP_TRACES_PATH):
            return endpoint + OTLP_HTTP_TRACES_PATH
        return endpoint
    if protocol in ("http/json", "http/protobuf"):
        return OTLP_HTTP_DEFAULT_ENDPOINT.rstrip("/") + OTLP_HTTP_TRACES_PATH
    return OTLP_GRPC_DEFAULT_ENDPOINT


def _is_otlp_traces_endpoint_set() -> bool:
    """True when user has set at least one of the OTLP endpoint env vars (Option A)."""
    for key in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            return True
    return False


class OTLPTraceExporterConfig:
    """
    Configuration for OTLP trace export.

    Enablement (Option A): OTLP is enabled when OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
    or OTEL_EXPORTER_OTLP_ENDPOINT is set. Single export: either Datadog agent or OTLP.
    """

    @property
    def otlp_traces_enabled(self) -> bool:
        """True when user has set an OTLP traces endpoint (Option A enablement)."""
        return _is_otlp_traces_endpoint_set()

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
        """Protocol: http/json, http/protobuf, or grpc. First version supports http/json only."""
        return _get_otel_traces_protocol()


config = OTLPTraceExporterConfig()
