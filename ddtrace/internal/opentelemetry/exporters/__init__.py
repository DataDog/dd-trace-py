from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from typing import Any

from ddtrace.internal.logger import get_logger
from ddtrace.internal.opentelemetry.exporters.exporter_telemetry import GRPCLogsExporter
from ddtrace.internal.opentelemetry.exporters.exporter_telemetry import GRPCMetricsExporter
from ddtrace.internal.opentelemetry.exporters.exporter_telemetry import HTTPLogsExporter
from ddtrace.internal.opentelemetry.exporters.exporter_telemetry import HTTPMetricsExporter


log = get_logger(__name__)

_MINIMUM_SUPPORTED_VERSION = (1, 15, 0)
_MINIMUM_LIGHTWEIGHT_VERSION = (1, 18, 0)


def get_metrics_exporter(protocol: str) -> type[Any] | None:
    """Return the metrics exporter class for protocol, if one is available."""
    try:
        exporter_version = _exporter_version()
        if not _is_supported(exporter_version):
            log.warning(
                "OpenTelemetry Metrics exporter for %s requires version %r or higher, but found version %r.",
                protocol,
                _MINIMUM_SUPPORTED_VERSION,
                exporter_version,
            )
            return None

        if protocol == "grpc":
            if _supports_lightweight_exporter(exporter_version):
                try:
                    from ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter import (
                        OTLPMetricExporter as GRPCMetricExporter,
                    )

                    return GRPCMetricExporter
                except ImportError:
                    pass
            import_module("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
            return GRPCMetricsExporter

        if protocol == "http/protobuf":
            if _supports_lightweight_exporter(exporter_version):
                from ddtrace.internal.opentelemetry.exporters.http_metric_exporter import (
                    OTLPMetricExporter as HTTPMetricExporter,
                )

                return HTTPMetricExporter
            import_module("opentelemetry.exporter.otlp.proto.http.metric_exporter")
            return HTTPMetricsExporter

        log.warning(
            "OpenTelemetry Metrics exporter protocol '%s' is not supported. Use 'grpc' or 'http/protobuf'.",
            protocol,
        )
    except ImportError as e:
        log.warning(
            "OpenTelemetry Metrics exporter for %s is not available. "
            "Install ddtrace[opentelemetry] before enabling OpenTelemetry Metrics support: %s",
            protocol,
            str(e),
        )
    return None


def get_logs_exporter(protocol: str) -> type[Any] | None:
    """Return the logs exporter class for protocol, if one is available."""
    try:
        exporter_version = _exporter_version()
        if not _is_supported(exporter_version):
            log.warning(
                "OpenTelemetry Logs exporter for %s requires version %r or higher, but found version %r. "
                "Please upgrade the appropriate opentelemetry-exporter package.",
                protocol,
                _MINIMUM_SUPPORTED_VERSION,
                exporter_version,
            )
            return None

        if protocol == "grpc":
            if _supports_lightweight_exporter(exporter_version):
                try:
                    from ddtrace.internal.opentelemetry.exporters.grpclib_log_exporter import (
                        OTLPLogExporter as GRPCLogExporter,
                    )

                    return GRPCLogExporter
                except ImportError:
                    pass
            import_module("opentelemetry.exporter.otlp.proto.grpc._log_exporter")
            return GRPCLogsExporter

        if protocol == "http/protobuf":
            if _supports_lightweight_exporter(exporter_version):
                from ddtrace.internal.opentelemetry.exporters.http_log_exporter import (
                    OTLPLogExporter as HTTPLogExporter,
                )

                return HTTPLogExporter
            import_module("opentelemetry.exporter.otlp.proto.http._log_exporter")
            return HTTPLogsExporter

        log.warning(
            "OpenTelemetry Logs exporter protocol '%s' is not supported. Use 'grpc' or 'http/protobuf'.",
            protocol,
        )
    except ImportError as e:
        log.warning(
            "OpenTelemetry Logs exporter for %s is not available. "
            "Install ddtrace[opentelemetry] before enabling OpenTelemetry Logs support: %s",
            protocol,
            str(e),
        )
    return None


def _exporter_version() -> str:
    try:
        return version("opentelemetry-exporter-otlp-proto-common")
    except PackageNotFoundError:
        from opentelemetry.exporter.otlp.proto.http.version import __version__

        return str(__version__)


def _is_supported(exporter_version: str) -> bool:
    return _parse_version(exporter_version) >= _MINIMUM_SUPPORTED_VERSION


def _supports_lightweight_exporter(exporter_version: str) -> bool:
    return _parse_version(exporter_version) >= _MINIMUM_LIGHTWEIGHT_VERSION


def _parse_version(exporter_version: str) -> tuple[int, int, int]:
    major, minor, patch = exporter_version.split(".")[:3]
    return int(major), int(minor), int(patch)
