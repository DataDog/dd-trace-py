"""Experimental OTLP transport shared by debugger output and symbol discovery."""

from functools import lru_cache
import json
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)
SCOPE = "com.datadoghq.dynamic_instrumentation"


class DebuggerTransport:
    def __init__(self, logger_provider: Any, meter_provider: Any, record_type: Any) -> None:
        self.logger_provider = logger_provider
        self.meter_provider = meter_provider
        self.logger = logger_provider.get_logger(SCOPE)
        self.meter = meter_provider.get_meter(SCOPE)
        self.record_type = record_type

    def emit(self, name: str, payload: dict[str, Any], tags: str = "") -> None:
        correlation = payload.get("dd", {})
        trace_id = correlation.get("trace_id", "0")
        self.logger.emit(
            self.record_type(
                event_name=name,
                timestamp=int(payload["timestamp"] * 1_000_000) if "timestamp" in payload else None,
                body=payload.get("message", name),
                trace_id=int(trace_id, 16 if len(trace_id) == 32 else 10),
                span_id=int(correlation.get("span_id", "0")),
                attributes={
                    "datadog.di.schema_version": 1,
                    "datadog.di.payload": json.dumps(payload),
                    "datadog.di.tags": tags,
                },
            )
        )

    def flush(self) -> bool:
        events = self.logger_provider.force_flush()
        metrics = self.meter_provider.force_flush()
        return bool(events and metrics)


@lru_cache(maxsize=1)
def get_debugger_transport() -> Optional[DebuggerTransport]:
    if not asbool(env.get("DD_DI_OTEL_ENABLED", "false")):
        return None

    try:
        from opentelemetry._logs import LogRecord
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        log.warning("DI OTLP mode requires the OpenTelemetry SDK and HTTP/protobuf exporter; keeping it disabled")
        return None

    resource = Resource.create(
        {
            k: v
            for k, v in {
                "service.name": config.service,
                "service.version": config.version,
                "deployment.environment.name": config.env,
            }.items()
            if v is not None
        }
    )
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=1000)],
    )
    return DebuggerTransport(logger_provider, meter_provider, LogRecord)
