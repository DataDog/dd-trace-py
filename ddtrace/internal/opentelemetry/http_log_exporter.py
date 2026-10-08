from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import logging
from typing import Any


try:
    from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
except ImportError:
    from opentelemetry.exporter.otlp.proto.common._internal._log_encoder import encode_logs

try:
    from opentelemetry.sdk._logs.export import LogRecordExporter as LogExporter
    from opentelemetry.sdk._logs.export import LogRecordExportResult as LogExportResult
except ImportError:
    from opentelemetry.sdk._logs.export import LogExporter
    from opentelemetry.sdk._logs.export import LogExportResult

from ddtrace.internal.opentelemetry.http_exporter import HttpExporter


log = logging.getLogger(__name__)


class OTLPLogExporter(LogExporter, HttpExporter):  # type: ignore[misc]
    """Export OTLP logs over HTTP without third-party HTTP packages."""

    def __init__(
        self,
        endpoint: str | None = None,
        certificate_file: str | None = None,
        client_key_file: str | None = None,
        client_certificate_file: str | None = None,
        headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None = None,
        timeout: float | None = None,
        compression: Any = None,
        max_request_size: int | None = None,
        **kwargs: Any,
    ) -> None:
        HttpExporter.__init__(
            self,
            "logs",
            "/v1/logs",
            endpoint,
            certificate_file,
            client_key_file,
            client_certificate_file,
            headers,
            timeout,
            compression,
            max_request_size,
        )

    def export(self, batch: Sequence[Any], *args: Any, **kwargs: Any) -> Any:
        try:
            payload = encode_logs(batch).SerializeToString()
        except Exception:
            log.exception("Failed to encode OpenTelemetry logs")
            return LogExportResult.FAILURE
        return self._export(payload, LogExportResult.SUCCESS, LogExportResult.FAILURE, kwargs.get("timeout_millis"))

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        HttpExporter.shutdown(self, timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return HttpExporter.force_flush(self, timeout_millis)
