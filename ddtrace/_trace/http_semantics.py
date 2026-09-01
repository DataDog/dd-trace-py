from typing import Any
from typing import Optional
from typing import Union
from typing import cast
from urllib import parse

from ddtrace._trace.otel_http_naming import set_otel_http_resource
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.constants import DEFAULT_SCHEME_PORTS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings._opentelemetry import _is_otlp_traces_exporter_enabled
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import redact_query_string
from ddtrace.internal.utils.http import redact_url
from ddtrace.internal.utils.http import strip_query_string


log = get_logger(__name__)

# Methods recognized by the OTel HTTP semantic conventions; all others become _OTHER.
_KNOWN_HTTP_METHODS = frozenset(
    ("GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH", "QUERY")
)
OTHER_HTTP_METHOD = "_OTHER"


def otel_number(value: int) -> Union[int, str]:
    """Preserve typed OTLP values while supporting the MsgPack string meta map."""
    return value if _is_otlp_traces_exporter_enabled(otel_config.exporter) else str(value)


@cached()
def normalize_http_method(method: str) -> tuple[str, Optional[str]]:
    """Return the normalized method and its original spelling when they differ."""
    upper = method.upper()
    if upper in _KNOWN_HTTP_METHODS:
        return upper, (None if upper == method else method)
    return OTHER_HTTP_METHOD, method


def _credentials_redacted_url(url: str) -> str:
    """Redact URL credentials without dropping the userinfo required by url.full."""
    if "@" not in url:
        return url

    parsed = parse.urlparse(url)
    netloc = parsed.netloc
    if "@" not in netloc:
        # The "@" belongs to the path or query, not userinfo.
        return url

    host = netloc[netloc.rindex("@") + 1 :]
    return parse.urlunparse(parsed._replace(netloc="REDACTED:REDACTED@" + host))


def _split_netloc(netloc: str, scheme: str) -> tuple[Optional[str], Optional[int]]:
    host = netloc.rsplit("@", 1)[-1]
    port: Optional[int] = None
    if host.startswith("["):
        close = host.find("]")
        if close == -1:
            return host or None, None
        maybe_port = host[close + 1 :]
        host = host[1:close]
        if maybe_port.startswith(":"):
            try:
                port = int(maybe_port[1:])
            except ValueError:
                port = None
    elif ":" in host:
        host, _, raw_port = host.rpartition(":")
        try:
            port = int(raw_port)
        except ValueError:
            port = None

    if port is None:
        port = DEFAULT_SCHEME_PORTS.get(scheme)

    return host or None, port


def _obfuscated_query(query: Optional[str]) -> Optional[Union[str, bytes]]:
    if not query:
        return None
    if config._global_query_string_obfuscation_disabled:
        return query
    pattern = config._obfuscation_query_string_pattern
    if pattern is None or getattr(pattern, "pattern", None) == b"":
        # obfuscation is disabled when DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP=""
        return None
    return redact_query_string(query, pattern)


def set_query_string_tag(span: Span, query: str) -> None:
    if not config._otel_trace_semantics_enabled:
        span._set_attribute(http.QUERY_STRING, query)
        return
    obfuscated = _obfuscated_query(query)
    if obfuscated:
        span._set_attribute(http.OTEL_URL_QUERY, cast(Any, obfuscated))


def set_url_tags_otel_server(
    integration_config: IntegrationConfig,
    span: Span,
    url: str,
    query: Optional[str],
    raw_uri: Optional[str] = None,
) -> None:
    parsed = parse.urlparse(url)
    if parsed.scheme:
        span._set_attribute(http.OTEL_URL_SCHEME, parsed.scheme)
    raw_path = None
    if raw_uri:
        try:
            raw_path = parse.urlparse(raw_uri).path
        except ValueError:
            # raw_uri is also forwarded unchanged to ASM. A malformed optional value must
            # not prevent the remaining request metadata from being reported.
            pass
    # url.path is required; an empty origin-form path is "/".
    span._set_attribute(http.OTEL_URL_PATH, raw_path or parsed.path or "/")

    address, port = _split_netloc(parsed.netloc, parsed.scheme)
    if address:
        span._set_attribute(net.SERVER_ADDRESS, address)
        if port is not None:
            span._set_attribute(net.SERVER_PORT, otel_number(port))

    # Either existing query-string option enables url.query capture.
    if not (integration_config.http_tag_query_string or integration_config.trace_query_string):
        return
    obfuscated = _obfuscated_query(query if query is not None else parsed.query)
    if obfuscated:
        span._set_attribute(http.OTEL_URL_QUERY, cast(Any, obfuscated))


def set_url_tags_otel_client(integration_config: IntegrationConfig, span: Span, url: str, query: Optional[str]) -> None:
    url = _credentials_redacted_url(url)
    parsed = parse.urlparse(url)

    if not (integration_config.http_tag_query_string or integration_config.trace_query_string):
        span._set_attribute(http.OTEL_URL_FULL, strip_query_string(url))
    elif config._global_query_string_obfuscation_disabled:
        span._set_attribute(http.OTEL_URL_FULL, url)
    elif (
        config._obfuscation_query_string_pattern is None
        or getattr(config._obfuscation_query_string_pattern, "pattern", None) == b""
    ):
        span._set_attribute(http.OTEL_URL_FULL, strip_query_string(url))
    else:
        span._set_attribute(
            http.OTEL_URL_FULL,
            cast(Any, redact_url(url, config._obfuscation_query_string_pattern, query)),
        )

    address, port = _split_netloc(parsed.netloc, parsed.scheme)
    if address:
        span._set_attribute(net.SERVER_ADDRESS, address)
        if port is not None:
            span._set_attribute(net.SERVER_PORT, otel_number(port))


# AIDEV-NOTE: This writer deliberately does not read the OTel semantics feature flag. Callers
# instantiate it only for the enabled path, keeping the decision at the per-call dispatch site.
class OTelHTTPSpanAttributes:
    __slots__ = ("_integration_config", "_normalized_method", "_original_method", "_span", "is_client")

    def __init__(self, span: Span, integration_config: IntegrationConfig) -> None:
        self._span = span
        self._integration_config = integration_config
        self._normalized_method: Optional[str] = None
        self._original_method: Optional[str] = None

        kind = span.get_tag(SPAN_KIND)
        self.is_client = kind == SpanKind.CLIENT if kind is not None else span.span_type == SpanTypes.HTTP

    def set_method(self, method: Optional[str]) -> None:
        if method is None:
            return

        normalized_method, original_method = normalize_http_method(method)
        self._normalized_method = normalized_method
        self._original_method = original_method
        self._span._set_attribute(http.OTEL_REQUEST_METHOD, normalized_method)
        if original_method is not None:
            self._span._set_attribute(http.OTEL_REQUEST_METHOD_ORIGINAL, original_method)

    def set_url(
        self,
        url: Optional[str],
        query: Optional[str] = None,
        raw_uri: Optional[str] = None,
        server_address: Optional[str] = None,
        fallback_server_address: Optional[str] = None,
    ) -> None:
        if url is not None:
            try:
                if self.is_client:
                    set_url_tags_otel_client(self._integration_config, self._span, url, query)
                else:
                    set_url_tags_otel_server(self._integration_config, self._span, url, query, raw_uri)
            except ValueError:
                # A malformed optional URL must not suppress metadata supplied separately.
                log.debug("failed to parse http url %r", url)

        if self._span.get_tag(net.SERVER_ADDRESS) is not None:
            return
        if server_address is not None:
            self._span._set_attribute(net.SERVER_ADDRESS, server_address)
        elif fallback_server_address is not None:
            self._span._set_attribute(net.SERVER_ADDRESS, fallback_server_address)

    def set_status_code(self, status_code: Optional[Union[int, str]]) -> None:
        if status_code is None:
            return
        try:
            int_status_code = int(status_code)
        except (TypeError, ValueError):
            log.debug("failed to convert http status code %r to int", status_code)
            return

        self._span._set_attribute(http.OTEL_RESPONSE_STATUS_CODE, otel_number(int_status_code))
        if not self._is_error_status(int_status_code):
            return

        self._span.error = 1
        # An exception carries more information than a status code, so the status code must
        # never overwrite an error.type that came from one.
        if self._span.get_tag(ERROR_TYPE) is None:
            self._span._set_attribute(ERROR_TYPE, str(int_status_code))

    def _is_error_status(self, status_code: int) -> bool:
        if self.is_client:
            return status_code >= 400
        if not config._http_server.error_statuses_configured:
            # OTel treats any code at or above 500 as an error.
            return status_code >= 500
        return bool(config._http_server.is_error_code(status_code))

    def set_user_agent(self, user_agent: Optional[str]) -> None:
        if user_agent:
            self._span._set_attribute(http.OTEL_USER_AGENT_ORIGINAL, user_agent)

    def set_client_addresses(
        self,
        client_address: Optional[str],
        network_peer_address: Optional[str],
    ) -> None:
        if client_address:
            self._span._set_attribute(http.OTEL_CLIENT_ADDRESS, client_address)
        if network_peer_address:
            self._span._set_attribute(net.NETWORK_PEER_ADDRESS, network_peer_address)

    def set_resource(self, route: Optional[str]) -> None:
        if self._normalized_method is None:
            return
        set_otel_http_resource(
            self._span,
            self._normalized_method,
            self._original_method,
            None if self.is_client else route,
        )


def set_url_tags_server(integration_config: IntegrationConfig, span: Span, url: str, query: Optional[str]) -> None:
    if config._otel_trace_semantics_enabled:
        set_url_tags_otel_server(integration_config, span, url, query)
    else:
        if not integration_config.http_tag_query_string:
            span._set_attribute(http.URL, strip_query_string(url))
        elif config._global_query_string_obfuscation_disabled:
            span._set_attribute(http.URL, url)
        elif (
            config._obfuscation_query_string_pattern is None
            or getattr(config._obfuscation_query_string_pattern, "pattern", None) == b""
        ):
            span._set_attribute(http.URL, strip_query_string(url))
        else:
            span._set_attribute(
                http.URL,
                cast(Any, redact_url(url, config._obfuscation_query_string_pattern, query)),
            )


def set_status_code_tag(span: Span, status_code: Union[int, str]) -> None:
    if not config._otel_trace_semantics_enabled:
        span._set_attribute(http.STATUS_CODE, str(status_code))
        return
    try:
        int_status_code = int(status_code)
    except (TypeError, ValueError):
        log.debug("failed to convert http status code %r to int", status_code)
        return
    span._set_attribute(http.OTEL_RESPONSE_STATUS_CODE, otel_number(int_status_code))


def set_method_tag(span: Span, method: str) -> None:
    if not config._otel_trace_semantics_enabled:
        span._set_attribute(http.METHOD, method)
        return
    normalized_method, original_method = normalize_http_method(method)
    span._set_attribute(http.OTEL_REQUEST_METHOD, normalized_method)
    if original_method is not None:
        span._set_attribute(http.OTEL_REQUEST_METHOD_ORIGINAL, original_method)


def server_url_tag() -> str:
    return http.OTEL_URL_PATH if config._otel_trace_semantics_enabled else http.URL


def http_block_metadata(
    method: Optional[str],
    status_code: Union[int, str],
    query: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if not config._otel_trace_semantics_enabled:
        metadata[http.STATUS_CODE] = str(status_code)
        if method is not None:
            metadata[http.METHOD] = method
        if query:
            metadata[http.QUERY_STRING] = query
        if user_agent:
            metadata[http.USER_AGENT] = user_agent
        return metadata

    metadata[http.OTEL_RESPONSE_STATUS_CODE] = otel_number(int(status_code))
    if method is not None:
        normalized_method, original_method = normalize_http_method(method)
        metadata[http.OTEL_REQUEST_METHOD] = normalized_method
        if original_method is not None:
            metadata[http.OTEL_REQUEST_METHOD_ORIGINAL] = original_method
    if query:
        obfuscated = _obfuscated_query(query)
        if obfuscated:
            metadata[http.OTEL_URL_QUERY] = cast(Any, obfuscated)
    if user_agent:
        metadata[http.OTEL_USER_AGENT_ORIGINAL] = user_agent
    return metadata


def user_agent_tag() -> str:
    return http.OTEL_USER_AGENT_ORIGINAL if config._otel_trace_semantics_enabled else http.USER_AGENT


def set_user_agent_tag(span: Span, user_agent: str) -> None:
    span._set_attribute(user_agent_tag(), user_agent)


def set_client_address_tags(span: Span, client_address: str) -> None:
    if config._otel_trace_semantics_enabled:
        span._set_attribute(http.OTEL_CLIENT_ADDRESS, client_address)
        span._set_attribute(net.NETWORK_PEER_ADDRESS, client_address)
    else:
        span._set_attribute(http.CLIENT_IP, client_address)
        span._set_attribute("network.client.ip", client_address)
