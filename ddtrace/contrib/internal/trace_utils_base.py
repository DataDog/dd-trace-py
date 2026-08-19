import re
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Union
from urllib import parse

from ddtrace._trace.span import Span
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.ext import user
from ddtrace.internal import core
from ddtrace.internal import span_bus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings._opentelemetry import _is_otlp_traces_exporter_enabled
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import normalize_header_name
from ddtrace.internal.utils.http import redact_query_string
from ddtrace.internal.utils.http import redact_url
from ddtrace.internal.utils.http import strip_query_string


log = get_logger(__name__)

NORMALIZE_PATTERN = re.compile(r"([^a-z0-9_\-:/]){1}")


@cached()
def _normalized_header_name(header_name: str) -> str:
    return NORMALIZE_PATTERN.sub("_", normalize_header_name(header_name))


def _normalize_tag_name(request_or_response: str, header_name: str) -> str:
    """
    Given a tag name, e.g. 'Content-Type', returns a corresponding normalized tag name, i.e
    'http.request.headers.content_type'. Rules applied actual header name are:
    - any letter is converted to lowercase
    - any digit is left unchanged
    - any block of any length of different ASCII chars is converted to a single underscore '_'
    :param request_or_response: The context of the headers: request|response
    :param header_name: The header's name
    :type header_name: str
    :rtype: str
    """
    # Looking at:
    #   - http://www.iana.org/assignments/message-headers/message-headers.xhtml
    #   - https://tools.ietf.org/html/rfc6648
    # and for consistency with other language integrations seems safe to assume the following algorithm for header
    # names normalization:
    #   - any letter is converted to lowercase
    #   - any digit is left unchanged
    #   - any block of any length of different ASCII chars is converted to a single underscore '_'
    normalized_name = _normalized_header_name(header_name)
    return "http.{}.headers.{}".format(request_or_response, normalized_name)


def _get_header_value_case_insensitive(headers: Mapping[str, str], keyname: str) -> Optional[str]:
    """
    Get a header in a case insensitive way. This function is meant for frameworks
    like Django < 2.2 that don't store the headers in a case insensitive mapping.
    """
    # just in case we are lucky
    shortcut_value = headers.get(keyname)
    if shortcut_value is not None:
        return shortcut_value

    for key, value in headers.items():
        if key.lower().replace("_", "-") == keyname:
            return value

    return None


# Possible User Agent header.
USER_AGENT_PATTERNS = ("http-user-agent", "user-agent")

# Datadog scan/test markers, tagged unconditionally so the API endpoint
# reducer can keep scan/test traffic out of the API inventory.
SECURITY_TESTING_HEADERS = ("x-datadog-endpoint-scan", "x-datadog-security-test")


def _get_request_header_user_agent(headers: Mapping[str, str], headers_are_case_sensitive: bool = False) -> str:
    """Get user agent from request headers
    :param headers: A dict of http headers to be stored in the span
    :type headers: dict or list
    """
    for key_pattern in USER_AGENT_PATTERNS:
        if not headers_are_case_sensitive:
            user_agent = headers.get(key_pattern)
        else:
            user_agent = _get_header_value_case_insensitive(headers, key_pattern)

        if user_agent:
            return user_agent
    return ""


def _store_security_testing_headers(
    headers: Mapping[str, str], span: Span, headers_are_case_sensitive: bool = False
) -> None:
    """Tag SECURITY_TESTING_HEADERS on the span, regardless of integration config."""
    for header_name in SECURITY_TESTING_HEADERS:
        if not headers_are_case_sensitive:
            value = headers.get(header_name)
        else:
            value = _get_header_value_case_insensitive(headers, header_name)
        if value is not None:
            span._set_attribute(_normalize_tag_name("request", header_name), value)


def set_user(
    tracer: Any,
    user_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    scope: Optional[str] = None,
    role: Optional[str] = None,
    session_id: Optional[str] = None,
    propagate: bool = False,
    span: Optional[Span] = None,
    may_block: bool = True,
    mode: str = "sdk",
):
    # type: (...) -> None
    """Set user tags.
    https://docs.datadoghq.com/logs/log_configuration/attributes_naming_convention/#user-related-attributes
    https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/?tab=set_tag&code-lang=python
    """
    if span is None:
        span = span_bus.get_root_span()
    if span:
        if user_id:
            str_user_id = str(user_id)
            span._set_attribute(user.ID, str_user_id)
            if propagate:
                span.context.dd_user_id = str_user_id

        # All other fields are optional
        if name:
            span._set_attribute(user.NAME, name)
        if email:
            span._set_attribute(user.EMAIL, email)
        if scope:
            span._set_attribute(user.SCOPE, scope)
        if role:
            span._set_attribute(user.ROLE, role)
        if session_id:
            span._set_attribute(user.SESSION_ID, session_id)

        if (may_block or mode == "auto") and asm_config._asm_enabled:
            exc = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                "set_user_for_asm", [user_id, mode, session_id]
            ).block_user.exception
            if exc:
                raise exc

    else:
        log.warning(
            "No root span in the current execution. Skipping set_user tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )


def _set_url_tag(integration_config: IntegrationConfig, span: Span, url: str, query: str) -> None:
    if not integration_config.http_tag_query_string:
        span._set_attribute(http.URL, strip_query_string(url))
    elif config._global_query_string_obfuscation_disabled:
        # TODO(munir): This case exists for backwards compatibility. To remove query strings from URLs,
        # users should set ``DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING=False``. This case should be
        # removed when config.global_query_string_obfuscation_disabled is removed (v3.0).
        span._set_attribute(http.URL, url)
    elif (
        config._obfuscation_query_string_pattern is None
        or getattr(config._obfuscation_query_string_pattern, "pattern", None) == b""
    ):
        # obfuscation is disabled when DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP=""
        span._set_attribute(http.URL, strip_query_string(url))
    else:
        span._set_attribute(http.URL, redact_url(url, config._obfuscation_query_string_pattern, query))


# OpenTelemetry HTTP semantic conventions, reachable only under DD_TRACE_OTEL_SEMANTICS_ENABLED.
# Obfuscation and redaction stay shared with the Datadog path above, so the flag moves attribute
# names without changing what a value contains.

# RFC 9110 methods plus PATCH and QUERY, which is the accepted set in the OTel HTTP
# semantic conventions. Anything else is reported as _OTHER.
_KNOWN_HTTP_METHODS = frozenset(
    ("GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH", "QUERY")
)
OTHER_HTTP_METHOD = "_OTHER"

# server.port is required whenever server.address is set, so a URL that omits the port falls
# back to the port the scheme implies.
_DEFAULT_SCHEME_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}

# Read once at import: set_http_meta is bound to its OTel variant there, not branched per span.
_OTEL_SEMANTICS = config._otel_trace_semantics_enabled

# The RFC requires server.port and http.response.status_code as integers over OTLP and strings
# in the MsgPack meta map. A span carries one value both encoders read, so pick it up front.
_OTEL_TYPED_VALUES = _OTEL_SEMANTICS and _is_otlp_traces_exporter_enabled(otel_config.exporter)


def _otel_number(value: int) -> Union[int, str]:
    """Represent an integer-typed OTel attribute for the exporter this process uses."""
    return value if _OTEL_TYPED_VALUES else str(value)


@cached()
def _normalize_http_method(method: str) -> tuple[str, Optional[str]]:
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

    The Datadog path uses _sanitized_url, which drops userinfo entirely. The OTel HTTP
    semantic conventions instead require the userinfo to survive in redacted form
    (https://REDACTED:REDACTED@host), so the credentials are substituted rather than cut.
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
        port = _DEFAULT_SCHEME_PORTS.get(scheme)

    return host or None, port


def _obfuscated_query(query: Optional[str]) -> Optional[Union[str, bytes]]:
    """Apply the configured query string obfuscation, or None if the query must not be reported.

    Mirrors the branches of _set_url_tag so that url.query and http.query.string are
    subject to exactly the same configuration.
    """
    if not query:
        return None
    if config._global_query_string_obfuscation_disabled:
        return query
    pattern = config._obfuscation_query_string_pattern
    if pattern is None or getattr(pattern, "pattern", None) == b"":
        # obfuscation is disabled when DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP=""
        return None
    return redact_query_string(query, pattern)


def _set_query_string_tag(span: Span, query: str) -> None:
    """Tag a query string an integration opted into outside of integration_config.

    aiohttp supports a per-app trace_query_string that set_http_meta cannot see, so the
    subscriber writes the tag itself. This keeps that side-write on the same attribute name
    (and the same obfuscation) as the query strings set_http_meta writes.
    """
    if not _OTEL_SEMANTICS:
        span._set_attribute(http.QUERY_STRING, query)
        return
    obfuscated = _obfuscated_query(query)
    if obfuscated:
        span._set_attribute(http.OTEL_URL_QUERY, obfuscated)


def _set_url_tags_otel_server(
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
    # url.path is Required, and the empty path of an origin-form request is "/"
    span._set_attribute(http.OTEL_URL_PATH, raw_path or parsed.path or "/")

    address, port = _split_netloc(parsed.netloc, parsed.scheme)
    if address:
        span._set_attribute(net.SERVER_ADDRESS, address)
        if port is not None:
            span._set_attribute(net.SERVER_PORT, _otel_number(port))

    # With http.url gone, url.query is the only place a server query string can live, so either
    # knob that used to permit capture still permits it.
    if not (integration_config.http_tag_query_string or integration_config.trace_query_string):
        return
    obfuscated = _obfuscated_query(query if query is not None else parsed.query)
    if obfuscated:
        span._set_attribute(http.OTEL_URL_QUERY, obfuscated)


def _set_url_tags_otel_client(
    integration_config: IntegrationConfig, span: Span, url: str, query: Optional[str]
) -> None:
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
        span._set_attribute(http.OTEL_URL_FULL, redact_url(url, config._obfuscation_query_string_pattern, query))

    address, port = _split_netloc(parsed.netloc, parsed.scheme)
    if address:
        span._set_attribute(net.SERVER_ADDRESS, address)
        if port is not None:
            span._set_attribute(net.SERVER_PORT, _otel_number(port))


def _set_url_tags_server(integration_config: IntegrationConfig, span: Span, url: str, query: Optional[str]) -> None:
    """Tag the request URL of a server span in whichever semantics mode is active.

    set_http_meta is swapped wholesale at import, but a handful of server integrations tag
    the URL outside of it (Django's request handler, AppSec's blocked-response paths), so
    they go through this dispatcher instead.
    """
    if _OTEL_SEMANTICS:
        _set_url_tags_otel_server(integration_config, span, url, query)
    else:
        _set_url_tag(integration_config, span, url, query)


def _set_status_code_tag(span: Span, status_code: Union[int, str]) -> None:
    """Tag an HTTP status code outside of set_http_meta, without touching span.error."""
    if not _OTEL_SEMANTICS:
        span._set_attribute(http.STATUS_CODE, str(status_code))
        return
    try:
        int_status_code = int(status_code)
    except (TypeError, ValueError):
        log.debug("failed to convert http status code %r to int", status_code)
        return
    span._set_attribute(http.OTEL_RESPONSE_STATUS_CODE, _otel_number(int_status_code))


def _set_method_tag(span: Span, method: str) -> None:
    """Tag an HTTP request method outside of set_http_meta."""
    if not _OTEL_SEMANTICS:
        span._set_attribute(http.METHOD, method)
        return
    normalized_method, original_method = _normalize_http_method(method)
    span._set_attribute(http.OTEL_REQUEST_METHOD, normalized_method)
    if original_method is not None:
        span._set_attribute(http.OTEL_REQUEST_METHOD_ORIGINAL, original_method)


# The attribute a server span's request path can be recovered from. url.path holds the path
# on its own, which is all the consumers of this name need.
SERVER_URL_TAG = http.OTEL_URL_PATH if _OTEL_SEMANTICS else http.URL


def _http_block_metadata(
    method: Optional[str],
    status_code: Union[int, str],
    query: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    """Attributes describing a request AppSec blocked, in the active semantics mode.

    Django gathers these into a dict and dispatches them to be applied to the request span
    verbatim, so they have to be spelled and shaped here rather than at the write site.
    """
    metadata: dict[str, Any] = {}
    if not _OTEL_SEMANTICS:
        metadata[http.STATUS_CODE] = str(status_code)
        if method is not None:
            metadata[http.METHOD] = method
        if query:
            metadata[http.QUERY_STRING] = query
        if user_agent:
            metadata[http.USER_AGENT] = user_agent
        return metadata

    metadata[http.OTEL_RESPONSE_STATUS_CODE] = _otel_number(int(status_code))
    if method is not None:
        normalized_method, original_method = _normalize_http_method(method)
        metadata[http.OTEL_REQUEST_METHOD] = normalized_method
        if original_method is not None:
            metadata[http.OTEL_REQUEST_METHOD_ORIGINAL] = original_method
    if query:
        obfuscated = _obfuscated_query(query)
        if obfuscated:
            metadata[http.OTEL_URL_QUERY] = obfuscated
    if user_agent:
        metadata[http.OTEL_USER_AGENT_ORIGINAL] = user_agent
    return metadata


# The attribute an HTTP request's user agent is reported under.
USER_AGENT_TAG = http.OTEL_USER_AGENT_ORIGINAL if _OTEL_SEMANTICS else http.USER_AGENT
