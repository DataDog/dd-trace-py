from typing import Any

from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER
from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER_VALUE


def get_sdk_compression(compression: str, protocol: str) -> Any:
    """Convert a validated compression name to the optional exporter's enum."""
    if protocol == "grpc":
        from grpc import Compression as GRPCCompression

        return {"none": GRPCCompression.NoCompression, "gzip": GRPCCompression.Gzip}[compression]

    from opentelemetry.exporter.otlp.proto.http import Compression as HTTPCompression

    return {
        "none": HTTPCompression.NoCompression,
        "gzip": HTTPCompression.Gzip,
        "deflate": HTTPCompression.Deflate,
    }[compression]


def set_http_exporter_marker(exporter: Any) -> None:
    client = getattr(exporter, "_client", None)
    if client is not None:
        client._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
        return

    exporter._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
    exporter._session.headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
