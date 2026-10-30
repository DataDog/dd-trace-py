from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import logging
from typing import Any

from opentelemetry.exporter.otlp.proto.common._internal.metrics_encoder import OTLPMetricExporterMixin
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from opentelemetry.sdk.metrics.export import AggregationTemporality
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.metrics.export import MetricExportResult
from opentelemetry.sdk.metrics.export import MetricsData

from ddtrace.internal.opentelemetry.exporters.exporter_telemetry import record_metrics_export_attempt
from ddtrace.internal.opentelemetry.exporters.exporter_telemetry import record_metrics_export_result
from ddtrace.internal.opentelemetry.exporters.http_exporter import HttpExporter


log = logging.getLogger(__name__)

_PROTOCOL = "http"


class OTLPMetricExporter(MetricExporter, OTLPMetricExporterMixin, HttpExporter):  # type: ignore[misc]
    """Export OTLP metrics over HTTP without third-party HTTP packages."""

    def __init__(
        self,
        endpoint: str | None = None,
        certificate_file: str | None = None,
        client_key_file: str | None = None,
        client_certificate_file: str | None = None,
        headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None = None,
        timeout: float | None = None,
        compression: Any = None,
        preferred_temporality: dict[type, AggregationTemporality] | None = None,
        preferred_aggregation: dict[type, Any] | None = None,
        max_request_size: int | None = None,
        **kwargs: Any,
    ) -> None:
        self._common_configuration(preferred_temporality)
        if preferred_aggregation:
            self._preferred_aggregation.update(preferred_aggregation)
        HttpExporter.__init__(
            self,
            "metrics",
            "/v1/metrics",
            endpoint,
            certificate_file,
            client_key_file,
            client_certificate_file,
            headers,
            timeout,
            compression,
            max_request_size,
        )

    def export(
        self, metrics_data: MetricsData, timeout_millis: float | None = 10_000, **kwargs: Any
    ) -> MetricExportResult:
        record_metrics_export_attempt(_PROTOCOL)
        log.debug("Exporting OpenTelemetry Metrics with %s protocol and protobuf encoding", _PROTOCOL)
        try:
            payload = encode_metrics(metrics_data).SerializeToString()
        except Exception:
            log.exception("Failed to encode OpenTelemetry metrics")
            result = MetricExportResult.FAILURE
        else:
            result = self._export(payload, MetricExportResult.SUCCESS, MetricExportResult.FAILURE, timeout_millis)
        record_metrics_export_result(result, _PROTOCOL)
        return result

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        HttpExporter.shutdown(self, timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return HttpExporter.force_flush(self, timeout_millis)
