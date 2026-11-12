from typing import Any

from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER
from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER_VALUE


def set_http_exporter_marker(exporter: Any) -> None:
    client = getattr(exporter, "_client", None)
    if client is not None:
        client._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
        return

    exporter._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
    exporter._session.headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
