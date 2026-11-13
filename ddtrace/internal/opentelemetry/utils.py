from typing import Any

from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER
from ddtrace.internal.constants import OTLP_HTTP_EXPORT_HEADER_VALUE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.settings._opentelemetry import _is_http
from ddtrace.internal.settings._opentelemetry import _targets_agentless_intake
from ddtrace.internal.settings._opentelemetry import _with_api_key


log = get_logger(__name__)


def prepare_sdk_exporter_env(
    signal: str, endpoint_env_var: str, headers_env_var: str, protocol: str, endpoint: str
) -> None:
    """Set the endpoint of a signal, and the Datadog OTLP intake credentials, where its SDK exporter reads them.

    The OpenTelemetry SDK exporters read the environment when they start, which can be after ddtrace resolved its
    configuration, so the user settings are read again and only the ones left unset are written.
    """
    if _targets_agentless_intake(endpoint_env_var):
        if not _is_http(protocol):
            log.warning(
                "Agentless mode exports OpenTelemetry %s to the Datadog OTLP intake over HTTP, but the "
                "%r protocol is configured. Set OTEL_EXPORTER_OTLP_PROTOCOL to http/protobuf, or point "
                "OTEL_EXPORTER_OTLP_ENDPOINT at a collector that speaks %r.",
                signal,
                protocol,
                protocol,
            )
        # Signal-specific headers replace the global ones, so carry those over rather than drop them.
        headers = env.get(headers_env_var) or env.get("OTEL_EXPORTER_OTLP_HEADERS") or ""
        if (headers_with_api_key := _with_api_key(headers)) != headers:
            env[headers_env_var] = headers_with_api_key
    if not (env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or env.get(endpoint_env_var)):
        env[endpoint_env_var] = endpoint


def set_http_exporter_marker(exporter: Any) -> None:
    client = getattr(exporter, "_client", None)
    if client is not None:
        client._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
        return

    exporter._headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
    exporter._session.headers[OTLP_HTTP_EXPORT_HEADER] = OTLP_HTTP_EXPORT_HEADER_VALUE
