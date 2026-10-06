from __future__ import annotations

import asyncio
from collections.abc import Mapping
from collections.abc import Sequence
import logging
import os
import ssl
import threading
from typing import Any
from urllib.parse import ParseResult
from urllib.parse import urlparse

from opentelemetry.util.re import parse_env_headers

from ddtrace.vendor.grpclib.client import Channel
from ddtrace.vendor.grpclib.client import UnaryUnaryMethod
from ddtrace.vendor.otel.exporter.otlp.common._aggregation import _get_aggregation
from ddtrace.vendor.otel.exporter.otlp.common._aggregation import _get_temporality
from ddtrace.vendor.otel.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from ddtrace.vendor.otel.proto.collector.metrics.v1 import metrics_service_pb2
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_CERTIFICATE
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_CLIENT_KEY
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_ENDPOINT
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_HEADERS
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_INSECURE
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_CERTIFICATE
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_COMPRESSION
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_HEADERS
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_INSECURE
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_METRICS_TIMEOUT
from ddtrace.vendor.otel.sdk.environment_variables import OTEL_EXPORTER_OTLP_TIMEOUT
from ddtrace.vendor.otel.sdk.metrics._internal.aggregation import Aggregation
from ddtrace.vendor.otel.sdk.metrics.export import AggregationTemporality
from ddtrace.vendor.otel.sdk.metrics.export import MetricExporter
from ddtrace.vendor.otel.sdk.metrics.export import MetricExportResult
from ddtrace.vendor.otel.sdk.metrics.export import MetricsData


log = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:4317"
_METHOD = "/opentelemetry.proto.collector.metrics.v1.MetricsService/Export"
ExportMetricsServiceRequest = getattr(metrics_service_pb2, "ExportMetricsServiceRequest")
ExportMetricsServiceResponse = getattr(metrics_service_pb2, "ExportMetricsServiceResponse")


class OTLPMetricExporter(MetricExporter):
    """OTLP/gRPC metrics exporter backed by the pure-Python grpclib client."""

    def __init__(
        self,
        endpoint: str | None = None,
        insecure: bool | None = None,
        headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None = None,
        timeout: float | None = None,
        preferred_temporality: dict[type, AggregationTemporality] | None = None,
        preferred_aggregation: dict[type, Aggregation] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            preferred_temporality=_get_temporality(preferred_temporality),
            preferred_aggregation=_get_aggregation(preferred_aggregation),
        )
        self._endpoint: str = (
            endpoint
            or os.environ.get(OTEL_EXPORTER_OTLP_METRICS_ENDPOINT)
            or os.environ.get(OTEL_EXPORTER_OTLP_ENDPOINT)
            or _DEFAULT_ENDPOINT
        )
        self._insecure = _resolve_insecure(self._endpoint, insecure)
        self._headers = _resolve_headers(headers)
        self._timeout = timeout or _environment_float(
            OTEL_EXPORTER_OTLP_METRICS_TIMEOUT, OTEL_EXPORTER_OTLP_TIMEOUT, 10
        )
        self._ssl_context = None if self._insecure else _build_ssl_context()
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._shutdown = False
        self._loop: asyncio.AbstractEventLoop
        self._channel: Channel
        self._method: UnaryUnaryMethod[Any, Any]
        self._initialize_transport()

        compression = os.environ.get(OTEL_EXPORTER_OTLP_METRICS_COMPRESSION, "none").strip().lower()
        if compression not in ("", "none"):
            raise ValueError("The grpclib OTLP metrics exporter does not support compression")

        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    def _initialize_transport(self) -> None:
        parsed = _parse_endpoint(self._endpoint)
        self._loop = asyncio.new_event_loop()
        self._channel = Channel(
            parsed.hostname,
            parsed.port or (443 if not self._insecure else 4317),
            loop=self._loop,
            ssl=self._ssl_context,
        )
        self._method = UnaryUnaryMethod(
            self._channel,
            _METHOD,
            ExportMetricsServiceRequest,
            ExportMetricsServiceResponse,
        )

    def _after_fork(self) -> None:
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._initialize_transport()

    def export(
        self, metrics_data: MetricsData, timeout_millis: float | None = 10_000, **kwargs: Any
    ) -> MetricExportResult:
        if self._shutdown:
            return MetricExportResult.FAILURE

        request = encode_metrics(metrics_data)
        timeout = self._timeout if timeout_millis is None else min(self._timeout, timeout_millis / 1000)
        try:
            with self._lock:
                if self._pid != os.getpid():
                    self._after_fork()
                self._loop.run_until_complete(self._method(request, timeout=timeout, metadata=self._headers))
            return MetricExportResult.SUCCESS
        except Exception:
            log.exception("Failed to export OpenTelemetry metrics over gRPC")
            return MetricExportResult.FAILURE

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        with self._lock:
            self._channel.close()
            if not self._loop.is_closed():
                self._loop.close()

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True


def _parse_endpoint(endpoint: str) -> ParseResult:
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
    if not parsed.hostname:
        raise ValueError(f"Invalid OTLP gRPC endpoint: {endpoint!r}")
    return parsed


def _resolve_insecure(endpoint: str, insecure: bool | None) -> bool:
    if insecure is not None:
        return insecure
    configured = os.environ.get(OTEL_EXPORTER_OTLP_METRICS_INSECURE) or os.environ.get(OTEL_EXPORTER_OTLP_INSECURE)
    if configured is not None:
        return configured.strip().lower() == "true"
    return _parse_endpoint(endpoint).scheme != "https"


def _resolve_headers(
    headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None,
) -> tuple[tuple[str, str], ...]:
    configured = (
        headers or os.environ.get(OTEL_EXPORTER_OTLP_METRICS_HEADERS) or os.environ.get(OTEL_EXPORTER_OTLP_HEADERS, "")
    )
    if isinstance(configured, str):
        return tuple(parse_env_headers(configured, liberal=True).items())
    if isinstance(configured, Mapping):
        return tuple(configured.items())
    return tuple(configured or ())


def _environment_float(signal_name: str, global_name: str, default: float) -> float:
    value = os.environ.get(signal_name) or os.environ.get(global_name)
    return float(value) if value is not None else default


def _build_ssl_context() -> ssl.SSLContext:
    certificate = os.environ.get(OTEL_EXPORTER_OTLP_METRICS_CERTIFICATE) or os.environ.get(
        OTEL_EXPORTER_OTLP_CERTIFICATE
    )
    context = ssl.create_default_context(cafile=certificate)
    client_certificate = os.environ.get(OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE) or os.environ.get(
        OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE
    )
    client_key = os.environ.get(OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY) or os.environ.get(OTEL_EXPORTER_OTLP_CLIENT_KEY)
    if client_certificate:
        context.load_cert_chain(client_certificate, client_key)
    return context
