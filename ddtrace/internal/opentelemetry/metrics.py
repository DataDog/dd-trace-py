import os

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def set_otel_meter_provider():
    """
    Get the selected OTLP exporter
    This function is intended to be called during application startup.
    """
    protocol = os.environ.get(
        "OTEL_EXPORTER_OTLP_PROTOCOL", os.environ.get("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "grpc").lower()
    )
    try:
        if "grpc" == protocol:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter as OTLPMetricExporterGRPC,
            )

            exporter = OTLPMetricExporterGRPC()

        elif "http/protobuf" == protocol:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter as OTLPMetricExporterHTTP,
            )

            exporter = OTLPMetricExporterHTTP()
        else:
            log.warning(
                "OpenTelemetry Metrics exporter protocol '%s' is not supported. "
                "Please use OTLP gRPC or HTTP/Protobuf exporter instead. "
                "Set OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=grpc or OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/protobuf",
                protocol,
            )
            return

        from opentelemetry.metrics import set_meter_provider
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        reader = PeriodicExportingMetricReader(exporter)
        set_meter_provider(MeterProvider(metric_readers=[reader]))
    except ImportError as e:
        log.warning(
            "The installed version of the OpenTelemetry SDK does not define required component: '%s'. "
            "A metrics provider (and exporter) will not be automatically configured. Open an issue at "
            "github.com/Datadog/dd-trace-py to report this bug.",
            str(e),
        )
