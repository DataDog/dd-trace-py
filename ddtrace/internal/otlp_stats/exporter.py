"""HTTP transport for OTLP trace-metrics payloads."""

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ddtrace.internal.logger import get_logger
from ddtrace.internal.otlp_stats import serializer
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.http import get_connection


if TYPE_CHECKING:
    from ddtrace.internal.otlp_stats.serializer import _Drained


log = get_logger(__name__)

METRICS_PATH = "/v1/metrics"


def _parse_headers(headers: str) -> "dict[str, str]":
    """Parse OTLP ``key=value,key=value`` header strings."""
    out: "dict[str, str]" = {}
    for pair in headers.split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def _resolve_url(endpoint: str) -> str:
    """Ensure the metrics endpoint targets the OTLP HTTP metrics path."""
    base = endpoint.rstrip("/")
    if base.endswith(METRICS_PATH):
        return base
    return base + METRICS_PATH


class OtlpStatsExporter:
    def __init__(self, endpoint: str, protocol: str, headers: str, timeout_ms: int, version: str) -> None:
        self._url = _resolve_url(endpoint)
        self._path = urlparse(self._url).path or "/"
        # We only speak HTTP; anything that is not explicitly http/json is sent as protobuf.
        self._protocol = "http/json" if protocol == "http/json" else "http/protobuf"
        self._headers = _parse_headers(headers)
        self._timeout = timeout_ms / 1000.0
        self._version = version

    def export(self, drained: "_Drained", bucket_size_ns: int, resource_attrs: "dict[str, str]") -> None:
        payload, content_type = serializer.serialize(
            drained, bucket_size_ns, resource_attrs, self._version, self._protocol
        )
        headers = dict(self._headers)
        headers["Content-Type"] = content_type
        telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.TRACERS, "trace_api.span_stats.export_attempts", 1, (("protocol", self._protocol),)
        )
        conn = None
        try:
            conn = get_connection(self._url, self._timeout)
            conn.request("POST", self._path, payload, headers)
            resp = conn.getresponse()
            resp.read()
        except Exception:
            log.debug("Failed to export OTLP trace metrics to %s", self._url, exc_info=True)
            self._count("trace_api.span_stats.export_errors")
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    log.debug("Failed to close OTLP trace metrics connection", exc_info=True)
        if resp.status >= 400:
            log.debug("OTLP trace metrics export to %s returned %d", self._url, resp.status)
            self._count("trace_api.span_stats.export_errors")
        else:
            self._count("trace_api.span_stats.export_successes")

    def _count(self, name: str) -> None:
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, name, 1, (("protocol", self._protocol),))
