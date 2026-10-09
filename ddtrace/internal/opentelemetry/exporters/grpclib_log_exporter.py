from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import logging
from typing import Any


try:
    from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
except ImportError:
    from opentelemetry.exporter.otlp.proto.common._internal._log_encoder import encode_logs
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse


try:
    from opentelemetry.sdk._logs.export import LogRecordExporter as LogExporter
    from opentelemetry.sdk._logs.export import LogRecordExportResult as LogExportResult
except ImportError:
    from opentelemetry.sdk._logs.export import LogExporter
    from opentelemetry.sdk._logs.export import LogExportResult

from ddtrace.internal.opentelemetry.exporters.exporter_telemetry import record_log_records
from ddtrace.internal.opentelemetry.exporters.grpclib_exporter import GrpclibExporter


log = logging.getLogger(__name__)

_METHOD = "/opentelemetry.proto.collector.logs.v1.LogsService/Export"
_PROTOCOL = "grpc"


class OTLPLogExporter(LogExporter, GrpclibExporter):  # type: ignore[misc]
    """Export OTLP logs over gRPC without depending on grpcio."""

    def __init__(
        self,
        endpoint: str | None = None,
        certificate_file: str | None = None,
        client_key_file: str | None = None,
        client_certificate_file: str | None = None,
        headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None = None,
        timeout: float | None = None,
        compression: Any = None,
        insecure: bool | None = None,
        **kwargs: Any,
    ) -> None:
        GrpclibExporter.__init__(
            self,
            "logs",
            _METHOD,
            ExportLogsServiceRequest,
            ExportLogsServiceResponse,
            endpoint,
            insecure,
            headers,
            timeout,
            compression,
            certificate_file,
            client_key_file,
            client_certificate_file,
        )

    def export(self, batch: Sequence[Any], *args: Any, **kwargs: Any) -> Any:
        record_log_records(len(batch), _PROTOCOL)
        log.debug("Exporting %d OpenTelemetry Logs with %s protocol and protobuf encoding", len(batch), _PROTOCOL)
        try:
            request = encode_logs(batch)
        except Exception:
            log.exception("Failed to encode OpenTelemetry logs")
            return LogExportResult.FAILURE
        timeout_millis = kwargs.get("timeout_millis")
        return self._export(request, LogExportResult.SUCCESS, LogExportResult.FAILURE, timeout_millis)

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        GrpclibExporter.shutdown(self, timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return GrpclibExporter.force_flush(self, timeout_millis)
