from typing import Any

from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER
from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER_VALUE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)


def get_exporter_compression(signal: str, protocol: str) -> Any:
    """Resolve compression explicitly so the SDK does not parse invalid environment values."""
    variable = f"OTEL_EXPORTER_OTLP_{signal}_COMPRESSION"
    configured = env.get(variable)
    if configured is None:
        variable = "OTEL_EXPORTER_OTLP_COMPRESSION"
        configured = env.get(variable, "none")
    compression = configured.strip().lower()

    # Keep optional exporter dependencies out of startup until an exporter is initialized.
    supported: dict[str, Any]
    if protocol == "grpc":
        from grpc import Compression as GRPCCompression

        supported = {"none": GRPCCompression.NoCompression, "gzip": GRPCCompression.Gzip}
    else:
        from opentelemetry.exporter.otlp.proto.http import Compression as HTTPCompression

        supported = {
            "none": HTTPCompression.NoCompression,
            "gzip": HTTPCompression.Gzip,
            "deflate": HTTPCompression.Deflate,
        }

    if compression not in supported:
        log.warning(
            "Unsupported value %r for %s with %s export. Falling back to no compression.",
            configured,
            variable,
            protocol,
        )
        compression = "none"
    return supported[compression]


def set_http_exporter_marker(exporter: Any) -> None:
    client = getattr(exporter, "_client", None)
    if client is not None:
        client._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
        return

    exporter._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
    exporter._session.headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
