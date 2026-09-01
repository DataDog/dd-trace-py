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

# RFC 9110 methods plus PATCH and QUERY, which is the accepted set in the OTel HTTP
# semantic conventions. Anything else is reported as _OTHER.
_KNOWN_HTTP_METHODS = frozenset(
    ("GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH", "QUERY")
)
OTHER_HTTP_METHOD = "_OTHER"


def otel_number(value: int) -> Union[int, str]:
    """Represent an integer-typed OTel attribute for the exporter this process uses.

    The RFC requires server.port and http.response.status_code as integers over OTLP and
    strings in the MsgPack meta map, and a span carries one value both encoders read.
    """
    return value if _is_otlp_traces_exporter_enabled(otel_config.exporter) else str(value)


@cached()
def normalize_http_method(method: str) -> tuple[str, Optional[str]]:
    """Split a raw HTTP method into (http.request.method, http.request.method_original).

    The second element is None when it would duplicate the first, which is the only case
    the semantic conventions allow http.request.method_original to be omitted. Case
    normalization counts as a difference, so "get" yields ("GET", "get").
    """
    upper = method.upper()
    if upper in _KNOWN_HTTP_METHODS:
        return upper, (None if upper == method else method)
    return OTHER_HTTP_METHOD, method


def _credentials_redacted_url(url: str) -> str:
    """Replace URL userinfo with REDACTED:REDACTED, as url.full requires.

    The Datadog path drops userinfo entirely. The OTel HTTP semantic conventions instead
    require the userinfo to survive in redacted form, so credentials are substituted.
    """
    if "@" not in url:
        return url

    parsed = parse.urlparse(url)
    netloc = parsed.netloc
    if "@" not in netloc:
        # userinfo is not what the "@" belongs to, for example a path or query containing it
        return url

    host = netloc[netloc.rindex("@") + 1 :]
    return parse.urlunparse(parsed._replace(netloc="REDACTED:REDACTED@" + host))


def _split_netloc(netloc: str, scheme: str) -> tuple[Optional[str], Optional[int]]:
    """Return (server.address, server.port) for a URL netloc."""
    host = netloc.rsplit("@", 1)[-1]
    port: Optional[int] = None
    if host.startswith("["):
        # IPv6 literal: [::1]:8080
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
    """Apply query obfuscation, or return None when the query must not be reported."""
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
    """Tag a query string an integration opted into outside of integration_config."""
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
    """Emit url.path, url.scheme, url.query, server.address and server.port for a server span."""
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
    # url.path is required, and the empty path of an origin-form request is "/".
    span._set_attribute(http.OTEL_URL_PATH, raw_path or parsed.path or "/")

    address, port = _split_netloc(parsed.netloc, parsed.scheme)
    if address:
        span._set_attribute(net.SERVER_ADDRESS, address)
        if port is not None:
            span._set_attribute(net.SERVER_PORT, otel_number(port))

    # With http.url gone, url.query is the only place a server query string can live, so either
    # knob that used to permit capture still permits it.
    if not (integration_config.http_tag_query_string or integration_config.trace_query_string):
        return
    obfuscated = _obfuscated_query(query if query is not None else parsed.query)
    if obfuscated:
        span._set_attribute(http.OTEL_URL_QUERY, cast(Any, obfuscated))


def set_url_tags_otel_client(integration_config: IntegrationConfig, span: Span, url: str, query: Optional[str]) -> None:
    """Emit url.full, server.address and server.port for a client span."""
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
    """Write the OTel HTTP attributes and resource owned by set_http_meta."""

    __slots__ = ("_integration_config", "_normalized_method", "_original_method", "_span", "is_client")

    def __init__(self, span: Span, integration_config: IntegrationConfig) -> None:
        self._span = span
        self._integration_config = integration_config
        self._normalized_method: Optional[str] = None
        self._original_method: Optional[str] = None

        kind = span.get_tag(SPAN_KIND)
        self.is_client = kind == SpanKind.CLIENT if kind is not None else span.span_type == SpanTypes.HTTP

    def set_method(self, method: Optional[str]) -> None:
        """Write the normalized method and retain it for final resource naming."""
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
        """Write URL attributes and server.address using URL, explicit, then fallback precedence."""
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
        """Write the typed status code and apply OTel client or server error semantics."""
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
            # OTel also treats an uninterpretable code above the standard 5xx range as an error.
            return status_code >= 500
        return bool(config._http_server.is_error_code(status_code))

    def set_user_agent(self, user_agent: Optional[str]) -> None:
        """Write user_agent.original when a request supplied one."""
        if user_agent:
            self._span._set_attribute(http.OTEL_USER_AGENT_ORIGINAL, user_agent)

    def set_client_addresses(
        self,
        client_address: Optional[str],
        network_peer_address: Optional[str],
    ) -> None:
        """Write the resolved client and direct network peer addresses."""
        if client_address:
            self._span._set_attribute(http.OTEL_CLIENT_ADDRESS, client_address)
        if network_peer_address:
            self._span._set_attribute(net.NETWORK_PEER_ADDRESS, network_peer_address)

    def set_resource(self, route: Optional[str]) -> None:
        """Finalize the OTel resource, allowing a later server route to refine it."""
        if self._normalized_method is None:
            return
        set_otel_http_resource(
            self._span,
            self._normalized_method,
            self._original_method,
            None if self.is_client else route,
        )


def set_url_tags_server(integration_config: IntegrationConfig, span: Span, url: str, query: Optional[str]) -> None:
    """Tag a server request URL in whichever semantics mode is active."""
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
    """Tag an HTTP status code outside set_http_meta, without touching span.error."""
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
    """Tag an HTTP request method outside set_http_meta."""
    if not config._otel_trace_semantics_enabled:
        span._set_attribute(http.METHOD, method)
        return
    normalized_method, original_method = normalize_http_method(method)
    span._set_attribute(http.OTEL_REQUEST_METHOD, normalized_method)
    if original_method is not None:
        span._set_attribute(http.OTEL_REQUEST_METHOD_ORIGINAL, original_method)


def server_url_tag() -> str:
    """Return the active attribute for recovering a server request path."""
    return http.OTEL_URL_PATH if config._otel_trace_semantics_enabled else http.URL


def http_block_metadata(
    method: Optional[str],
    status_code: Union[int, str],
    query: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    """Return blocked-request attributes in the active semantics mode."""
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
    """Return the active HTTP request user-agent attribute."""
    return http.OTEL_USER_AGENT_ORIGINAL if config._otel_trace_semantics_enabled else http.USER_AGENT


def set_user_agent_tag(span: Span, user_agent: str) -> None:
    """Write the HTTP request user-agent attribute for the active semantics."""
    span._set_attribute(user_agent_tag(), user_agent)


def set_client_address_tags(span: Span, client_address: str) -> None:
    """Write service-entry client address attributes for the active semantics."""
    if config._otel_trace_semantics_enabled:
        span._set_attribute(http.OTEL_CLIENT_ADDRESS, client_address)
        span._set_attribute(net.NETWORK_PEER_ADDRESS, client_address)
    else:
        span._set_attribute(http.CLIENT_IP, client_address)
        span._set_attribute("network.client.ip", client_address)
