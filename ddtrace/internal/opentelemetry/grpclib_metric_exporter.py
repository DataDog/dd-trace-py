from __future__ import annotations

import asyncio
from collections.abc import Mapping
from collections.abc import Sequence
import logging
import os
import ssl
from time import monotonic
from time import sleep
from typing import Any
from urllib.parse import ParseResult
from urllib.parse import urlparse
import warnings

from grpclib.client import Channel
from grpclib.client import UnaryUnaryMethod
from grpclib.const import Status
from grpclib.exceptions import GRPCError
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
from opentelemetry.sdk.metrics.export import AggregationTemporality
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.metrics.export import MetricExportResult
from opentelemetry.sdk.metrics.export import MetricsData
from opentelemetry.util.re import parse_env_headers

from ddtrace.internal import forksafe
from ddtrace.internal.settings import env
from ddtrace.internal.threads import Lock


log = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:4317"
_METHOD = "/opentelemetry.proto.collector.metrics.v1.MetricsService/Export"
_MAX_RETRY_DELAY = 64.0
_OTLP_CERTIFICATE = "OTEL_EXPORTER_OTLP_CERTIFICATE"
_OTLP_COMPRESSION = "OTEL_EXPORTER_OTLP_COMPRESSION"
_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
_OTLP_HEADERS = "OTEL_EXPORTER_OTLP_HEADERS"
_OTLP_TIMEOUT = "OTEL_EXPORTER_OTLP_TIMEOUT"
_OTLP_METRICS_CERTIFICATE = "OTEL_EXPORTER_OTLP_METRICS_CERTIFICATE"
_OTLP_METRICS_CLIENT_CERTIFICATE = "OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE"
_OTLP_METRICS_CLIENT_KEY = "OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY"
_OTLP_METRICS_COMPRESSION = "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION"
_OTLP_METRICS_ENDPOINT = "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"
_OTLP_METRICS_HEADERS = "OTEL_EXPORTER_OTLP_METRICS_HEADERS"
_OTLP_METRICS_INSECURE = "OTEL_EXPORTER_OTLP_METRICS_INSECURE"
_OTLP_METRICS_TIMEOUT = "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT"
_RETRYABLE_STATUSES = {
    Status.CANCELLED,
    Status.DEADLINE_EXCEEDED,
    Status.RESOURCE_EXHAUSTED,
    Status.ABORTED,
    Status.OUT_OF_RANGE,
    Status.UNAVAILABLE,
    Status.DATA_LOSS,
}


class OTLPMetricExporter(MetricExporter):  # type: ignore[misc]
    """Export OTLP metrics over gRPC without depending on grpcio."""

    def __init__(
        self,
        endpoint: str | None = None,
        insecure: bool | None = None,
        headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None = None,
        timeout: float | None = None,
        compression: Any = None,
        preferred_temporality: dict[type, AggregationTemporality] | None = None,
        preferred_aggregation: dict[type, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            preferred_temporality=preferred_temporality,
            preferred_aggregation=preferred_aggregation,
        )
        self._endpoint = endpoint or _environment(
            _OTLP_METRICS_ENDPOINT,
            _OTLP_ENDPOINT,
            _DEFAULT_ENDPOINT,
        )
        self._insecure = _resolve_insecure(self._endpoint, insecure)
        self._headers = _resolve_headers(headers)
        self._timeout = (
            timeout
            if timeout is not None
            else _environment_float(
                _OTLP_METRICS_TIMEOUT,
                _OTLP_TIMEOUT,
                10.0,
            )
        )
        configured_compression = compression or _environment(
            _OTLP_METRICS_COMPRESSION,
            _OTLP_COMPRESSION,
            "none",
        )
        compression_name = str(getattr(configured_compression, "value", configured_compression)).lower()
        if compression_name not in ("", "none"):
            raise ValueError("The grpclib OTLP metrics exporter does not support compression")

        self._ssl_context = None if self._insecure else _build_ssl_context()
        self._lock = Lock()
        self._pid = os.getpid()
        self._shutdown = False
        self._initialize_transport()
        forksafe.register(self._after_fork)

    def _initialize_transport(self) -> None:
        parsed = _parse_endpoint(self._endpoint)
        self._loop = asyncio.new_event_loop()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="The loop argument is deprecated", category=DeprecationWarning)
            self._channel = Channel(
                parsed.hostname,
                parsed.port or (443 if not self._insecure else 4317),
                loop=self._loop,
                ssl=self._ssl_context,
            )
        self._method: UnaryUnaryMethod[Any, Any] = UnaryUnaryMethod(
            self._channel,
            _METHOD,
            ExportMetricsServiceRequest,
            ExportMetricsServiceResponse,
        )

    def _after_fork(self) -> None:
        self._pid = os.getpid()
        self._lock = Lock()
        self._initialize_transport()

    def export(
        self, metrics_data: MetricsData, timeout_millis: float | None = 10_000, **kwargs: Any
    ) -> MetricExportResult:
        if self._shutdown:
            return MetricExportResult.FAILURE

        try:
            request = encode_metrics(metrics_data)
        except Exception:
            log.exception("Failed to encode OpenTelemetry metrics")
            return MetricExportResult.FAILURE

        timeout = self._timeout if timeout_millis is None else min(self._timeout, timeout_millis / 1000)
        deadline = monotonic() + timeout
        delay = 1.0
        while True:
            try:
                with self._lock:
                    if self._pid != os.getpid():
                        self._after_fork()
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return MetricExportResult.FAILURE
                    self._loop.run_until_complete(self._method(request, timeout=remaining, metadata=self._headers))
                return MetricExportResult.SUCCESS
            except GRPCError as error:
                remaining = deadline - monotonic()
                if error.status not in _RETRYABLE_STATUSES or remaining <= delay:
                    log.error("Failed to export OpenTelemetry metrics over gRPC: %s", error)
                    return MetricExportResult.FAILURE
                log.warning("Transient gRPC error exporting OpenTelemetry metrics; retrying in %ss", delay)
                sleep(delay)
                delay = min(delay * 2, _MAX_RETRY_DELAY)
            except Exception:
                log.exception("Failed to export OpenTelemetry metrics over gRPC")
                return MetricExportResult.FAILURE

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        forksafe.unregister(self._after_fork)
        with self._lock:
            self._channel.close()
            if not self._loop.is_closed():
                self._loop.close()

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True


def _parse_endpoint(endpoint: str) -> ParseResult:
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
    if not parsed.hostname or parsed.scheme not in ("", "http", "https"):
        raise ValueError(f"Invalid OTLP gRPC endpoint: {endpoint!r}")
    if parsed.path not in ("", "/"):
        raise ValueError("OTLP gRPC endpoints cannot include a path")
    return parsed


def _environment(signal_name: str, global_name: str, default: str) -> str:
    return env.get(signal_name) or env.get(global_name) or default


def _resolve_insecure(endpoint: str, insecure: bool | None) -> bool:
    if insecure is not None:
        return insecure
    configured = env.get(_OTLP_METRICS_INSECURE)
    if configured is not None:
        return configured.strip().lower() == "true"
    return _parse_endpoint(endpoint).scheme != "https"


def _resolve_headers(
    headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None,
) -> tuple[tuple[str, str], ...]:
    configured = (
        headers
        if headers is not None
        else _environment(
            _OTLP_METRICS_HEADERS,
            _OTLP_HEADERS,
            "",
        )
    )
    if isinstance(configured, str):
        return tuple(parse_env_headers(configured).items())
    if isinstance(configured, Mapping):
        return tuple(configured.items())
    return tuple(configured)


def _environment_float(signal_name: str, global_name: str, default: float) -> float:
    configured = env.get(signal_name) or env.get(global_name)
    return float(configured) if configured is not None else default


def _build_ssl_context() -> ssl.SSLContext:
    certificate = env.get(_OTLP_METRICS_CERTIFICATE) or env.get(_OTLP_CERTIFICATE)
    context = ssl.create_default_context(cafile=certificate)
    client_certificate = env.get(_OTLP_METRICS_CLIENT_CERTIFICATE)
    client_key = env.get(_OTLP_METRICS_CLIENT_KEY)
    if client_certificate:
        context.load_cert_chain(client_certificate, client_key)
    return context
