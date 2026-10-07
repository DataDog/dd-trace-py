from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import logging
from typing import Any

from opentelemetry.exporter.otlp.proto.common._internal.metrics_encoder import OTLPMetricExporterMixin
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
from opentelemetry.sdk.metrics.export import AggregationTemporality
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.metrics.export import MetricExportResult
from opentelemetry.sdk.metrics.export import MetricsData

from ddtrace.internal.opentelemetry.grpclib_exporter import GrpclibExporter


log = logging.getLogger(__name__)

_METHOD = "/opentelemetry.proto.collector.metrics.v1.MetricsService/Export"


class OTLPMetricExporter(MetricExporter, OTLPMetricExporterMixin, GrpclibExporter):  # type: ignore[misc]
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
        self._common_configuration(preferred_temporality)
        if preferred_aggregation:
            self._preferred_aggregation.update(preferred_aggregation)
        GrpclibExporter.__init__(
            self,
            "metrics",
            _METHOD,
            ExportMetricsServiceRequest,
            ExportMetricsServiceResponse,
            endpoint,
            insecure,
            headers,
            timeout,
            compression,
        )

    def export(
        self, metrics_data: MetricsData, timeout_millis: float | None = 10_000, **kwargs: Any
    ) -> MetricExportResult:
        try:
            request = encode_metrics(metrics_data)
        except Exception:
            log.exception("Failed to encode OpenTelemetry metrics")
            return MetricExportResult.FAILURE
        return self._export(request, MetricExportResult.SUCCESS, MetricExportResult.FAILURE, timeout_millis)

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        GrpclibExporter.shutdown(self, timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return GrpclibExporter.force_flush(self, timeout_millis)
