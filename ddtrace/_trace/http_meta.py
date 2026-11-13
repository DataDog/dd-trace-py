"""HTTP span metadata shared by tracing subscribers and integrations."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import ipaddress
import re
from typing import Any
from typing import Optional
from typing import Union
from urllib import parse

from ddtrace._trace.http_tags import set_client_address_tags
from ddtrace._trace.otel.http.tags import OTelHTTPSpanAttributes
from ddtrace._trace.otel.http.tags import _sanitized_url as _sanitize_url_tag
from ddtrace._trace.span import Span
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.compat import ip_is_global
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import normalize_header_name


log = get_logger(__name__)

REQUEST = "request"
RESPONSE = "response"

# Tag normalization based on: https://docs.datadoghq.com/tagging/#defining-tags
# With the exception of '.' in header names which are replaced with '_' to avoid
# starting a "new object" on the UI.
NORMALIZE_PATTERN = re.compile(r"([^a-z0-9_\-:/]){1}")


IP_PATTERNS = (
    "x-forwarded-for",
    "x-real-ip",
    "true-client-ip",
    "x-client-ip",
    "forwarded",
    "forwarded-for",
    "x-cluster-client-ip",
    "fastly-client-ip",
    "cf-connecting-ip",
    "cf-connecting-ipv6",
)


# Possible User Agent header.
USER_AGENT_PATTERNS = ("http-user-agent", "user-agent")

# Datadog scan/test markers, tagged unconditionally so the API endpoint
# reducer can keep scan/test traffic out of the API inventory.
SECURITY_TESTING_HEADERS = ("x-datadog-endpoint-scan", "x-datadog-security-test")


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
    return f"http.{request_or_response}.headers.{normalized_name}"


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


def _set_url_tag(integration_config: IntegrationConfig, span: SpanData, url: str, query: Optional[str]) -> None:
    # String URL inputs produce string output; the sanitizer also accepts bytes.
    span._set_attribute(
        http.URL,
        _sanitize_url_tag(url, query, integration_config.http_tag_query_string),  # type: ignore[arg-type]
    )


def _store_headers(
    headers: Mapping[str, str], span: Span, integration_config: Optional[IntegrationConfig], request_or_response: str
) -> None:
    """
    :param headers: A dict of http headers to be stored in the span
    :type headers: dict or list
    :param span: The Span instance where tags will be stored
    :type span: ddtrace.trace.Span
    :param integration_config: An integration specific config object.
    :type integration_config: ddtrace.settings.IntegrationConfig
    """
    if not isinstance(headers, dict):
        try:
            headers = dict(headers)
        except Exception:
            return

    if integration_config is None:
        log.debug("Skipping headers tracing as no integration config was provided")
        return

    for header_name, header_value in headers.items():
        # config._header_tag_name gets an element of the dictionary in config._trace_http_header_tags
        # which gets the value from DD_TRACE_HEADER_TAGS environment variable."""
        tag_name = integration_config._header_tag_name(header_name)
        if tag_name is None:
            continue
        # An empty tag defaults to a http.<request or response>.headers.<header name> tag
        span._set_attribute(tag_name or _normalize_tag_name(request_or_response, header_name), header_value)


def _get_request_header_referrer_host(headers: Mapping[str, str], headers_are_case_sensitive: bool = False) -> str:
    """Get referrer host from request headers
    :param headers: A dict of http headers to be stored in the span
    :type headers: dict or list
    :param headers_are_case_sensitive: Whether the headers are case sensitive
    :type headers_are_case_sensitive: bool
    :return: The referrer host if found, empty string otherwise
    :rtype: str
    """
    if headers_are_case_sensitive:
        referrer = _get_header_value_case_insensitive(headers, http.REFERER_HEADER)
    else:
        referrer = headers.get(http.REFERER_HEADER)
    if referrer:
        try:
            parsed_url = parse.urlparse(referrer)
            if parsed_url.hostname:
                return parsed_url.hostname
        except (ValueError, AttributeError):
            return ""
    return ""


def _parse_ip_header(ip_header_value: str) -> str:
    """Parse the ip header, either in Forwarded-For format or Forwarded format.

    references: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Forwarded
    """
    IP_EXTRACTIONS = [
        r"^\s*(?P<ip>[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)$",  # ipv4 simple format
        r'(?:^|;)\s*for="?(?P<ip>[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)',  # ipv4 forwarded format
        r"^\s*(?P<ip>[0-9a-fA-F:]+)$",  # ipv6 simple format
        r'(?:^|;)\s*for="\[(?P<ip>[0-9a-fA-F:]+)\]',  # ipv6 forwarded format
    ]
    for pattern in IP_EXTRACTIONS:
        if m := re.search(pattern, ip_header_value, re.IGNORECASE):
            return m.group("ip")
    return ""


def _get_request_header_client_ip(
    headers: Optional[Mapping[str, str]], peer_ip: Optional[str] = None, headers_are_case_sensitive: bool = False
) -> Optional[str]:
    if not headers:
        try:
            _ = ipaddress.ip_address(str(peer_ip))
        except ValueError:
            return ""
        return peer_ip

    def get_header_value(key: str) -> Optional[str]:
        if not headers_are_case_sensitive:
            return headers.get(key)

        return _get_header_value_case_insensitive(headers, key)

    ip_header_value: Optional[str] = ""
    user_configured_ip_header = config._client_ip_header
    if user_configured_ip_header:
        # Used selected the header to use to get the IP
        ip_header_value = get_header_value(
            user_configured_ip_header.lower().replace("_", "-")
            if headers_are_case_sensitive
            else user_configured_ip_header
        )
        if not ip_header_value:
            log.debug("DD_TRACE_CLIENT_IP_HEADER configured but '%s' header missing", user_configured_ip_header)
            return ""

        try:
            _ = ipaddress.ip_address(str(ip_header_value))
        except ValueError:
            log.debug("Invalid IP address from configured %s header: %s", user_configured_ip_header, ip_header_value)
            return ""

    else:
        if headers_are_case_sensitive:
            new_headers = {k.lower().replace("_", "-"): v for k, v in headers.items()}
            for ip_header in IP_PATTERNS:
                if ip_header in new_headers:
                    ip_header_value = new_headers[ip_header]
                    break
        else:
            for ip_header in IP_PATTERNS:
                if ip_header in headers:
                    ip_header_value = headers[ip_header]
                    break

    private_ip_from_headers = ""

    if ip_header_value:
        # At this point, we have one IP header, check its value and retrieve the first public IP

        ip_list = ip_header_value.split(",")
        for ip in ip_list:
            ip = _parse_ip_header(ip)
            if not ip:
                continue
            try:
                if ip_is_global(ip):
                    return ip
                elif not private_ip_from_headers:
                    # IP is private, store it just in case we don't find a public one later
                    private_ip_from_headers = ip
            except ValueError:  # invalid IP
                continue

    # At this point we have none or maybe one private ip from the headers: check the peer ip in
    # case it's public and, if not, return either the private_ip from the headers (if we have one)
    # or the peer private ip
    try:
        # Missing addresses are rejected with ValueError, like other invalid addresses.
        if ip_is_global(peer_ip) or not private_ip_from_headers:  # type: ignore[arg-type]
            return peer_ip
    except ValueError:
        pass

    return private_ip_from_headers


def _store_request_headers(headers: dict[str, str], span: Span, integration_config: IntegrationConfig) -> None:
    """
    Store request headers as a span's tags
    :param headers: All the request's http headers, will be filtered through the whitelist
    :type headers: dict or list
    :param span: The Span instance where tags will be stored
    :type span: ddtrace.trace.Span
    :param integration_config: An integration specific config object.
    :type integration_config: ddtrace.settings.IntegrationConfig
    """
    _store_headers(headers, span, integration_config, REQUEST)


def _store_response_headers(headers: Mapping[str, str], span: Span, integration_config: IntegrationConfig) -> None:
    """
    Store response headers as a span's tags
    :param headers: All the response's http headers, will be filtered through the whitelist
    :type headers: dict or list
    :param span: The Span instance where tags will be stored
    :type span: ddtrace.trace.Span
    :param integration_config: An integration specific config object.
    :type integration_config: ddtrace.settings.IntegrationConfig
    """
    _store_headers(headers, span, integration_config, RESPONSE)


def _sanitized_url(url: str) -> str:
    """
    Sanitize url by removing parts with potential auth info
    """
    if "@" in url:
        parsed = parse.urlparse(url)
        netloc = parsed.netloc

        if "@" not in netloc:
            # Safe url, `@` not in netloc
            return url

        netloc = netloc[netloc.index("@") + 1 :]
        return parse.urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                "",
                parsed.query,
                "",
            )
        )

    return url


def set_http_meta(
    span: Span,
    integration_config: IntegrationConfig,
    method: Optional[str] = None,
    url: Optional[str] = None,
    target_host: Optional[str] = None,
    server_address: Optional[str] = None,
    status_code: Optional[Union[int, str]] = None,
    status_msg: Optional[str] = None,
    query: Optional[str] = None,
    parsed_query: Optional[Mapping[str, str]] = None,
    request_headers: Optional[Mapping[str, str]] = None,
    response_headers: Optional[Mapping[str, str]] = None,
    retries_remain: Optional[Union[int, str]] = None,
    raw_uri: Optional[str] = None,
    request_cookies: Optional[Mapping[str, Union[str, list[str]]]] = None,
    request_path_params: Optional[Union[Mapping[str, Any], Sequence[Any]]] = None,
    request_body: Optional[Union[str, dict[str, list[str]]]] = None,
    peer_ip: Optional[str] = None,
    headers_are_case_sensitive: bool = False,
    route: Optional[str] = None,
    response_cookies: Optional[dict[str, str]] = None,
    query_tagged_separately: bool = False,
) -> None:
    """
    Set HTTP metas on the span

    :param method: the HTTP method
    :param url: the HTTP URL
    :param status_code: the HTTP status code
    :param status_msg: the HTTP status message
    :param query: the HTTP query part of the URI as a string
    :param query_tagged_separately: the caller tags the query string itself, so it must not be read from the URL
    :param parsed_query: the HTTP query part of the URI as parsed by the framework and forwarded to the user code
    :param request_headers: the HTTP request headers
    :param response_headers: the HTTP response headers
    :param raw_uri: the full raw HTTP URI (including ports and query)
    :param request_cookies: the HTTP request cookies as a dict
    :param request_path_params: the parameters of the HTTP URL as set by the framework. Polymorphic: a mapping of
        ``{name: value}`` for frameworks that bind named parameters (Django ``resolver_match.kwargs``, Flask
        ``view_args``, ...), or a positional sequence of values for regex routes with only unnamed captures (Django
        ``resolver_match.args``, Tornado ``path_args``).
    """
    otel_http = OTelHTTPSpanAttributes(span, integration_config) if config._otel_trace_semantics_enabled else None
    if otel_http is not None:
        _set_http_meta_otel(
            otel_http,
            method=method,
            url=url,
            raw_uri=raw_uri,
            target_host=target_host,
            server_address=server_address,
            status_code=status_code,
            query=query,
            query_tagged_separately=query_tagged_separately,
            request_headers=request_headers,
            headers_are_case_sensitive=headers_are_case_sensitive,
            route=route,
        )
    else:
        _set_http_meta(
            span,
            integration_config,
            method=method,
            url=url,
            target_host=target_host,
            server_address=server_address,
            status_code=status_code,
            query=query,
            request_headers=request_headers,
            headers_are_case_sensitive=headers_are_case_sensitive,
        )

    if status_msg is not None:
        span._set_attribute(http.STATUS_MSG, status_msg)

    request_ip = peer_ip
    if request_headers:
        # Extract referrer host if referer header is present
        referrer_host = _get_request_header_referrer_host(request_headers, headers_are_case_sensitive)
        if referrer_host:
            span._set_attribute(http.REFERRER_HOSTNAME, referrer_host)

        _store_security_testing_headers(request_headers, span, headers_are_case_sensitive)

        if integration_config.is_header_tracing_configured:
            """We should store both http.<request_or_response>.headers.<header_name> and
            http.<key>. The last one
            is the DD standardized tag for user-agent"""
            _store_request_headers(dict(request_headers), span, integration_config)

    # We always collect the IP if appsec is enabled to report it on potential vulnerabilities.
    # https://datadoghq.atlassian.net/wiki/spaces/APS/pages/2118779066/Client+IP+addresses+resolution
    if asm_config._asm_enabled or config._retrieve_client_ip:
        # Retrieve the IP if it was calculated on AppSecProcessor.on_span_start
        request_ip = core.find_item("http.request.remote_ip")

        if not request_ip:
            # Not calculated: framework does not support IP blocking or testing env
            request_ip = _get_request_header_client_ip(request_headers, peer_ip, headers_are_case_sensitive) or peer_ip

        if otel_http is None or not otel_http.is_client:
            # Under OTel semantics an outgoing request has no client address, so client spans report none.
            set_client_address_tags(span, request_ip, peer_ip)

    if response_headers is not None and integration_config.is_header_tracing_configured:
        _store_response_headers(response_headers, span, integration_config)

    if retries_remain is not None:
        span._set_attribute(http.RETRIES_REMAIN, str(retries_remain))

    core.dispatch(
        "set_http_meta_for_asm",
        (
            span,
            request_ip,
            raw_uri,
            route,
            method,
            request_headers,
            request_cookies,
            parsed_query,
            request_path_params,
            request_body,
            status_code,
            response_headers,
            response_cookies,
            peer_ip,
            headers_are_case_sensitive,
        ),
    )

    if route is not None:
        span._set_attribute(http.ROUTE, route)


def _set_http_meta(
    span: Span,
    integration_config: IntegrationConfig,
    method: Optional[str],
    url: Optional[str],
    target_host: Optional[str],
    server_address: Optional[str],
    status_code: Optional[Union[int, str]],
    query: Optional[str],
    request_headers: Optional[Mapping[str, str]],
    headers_are_case_sensitive: bool,
) -> None:
    """Set the tags whose names or semantics differ from OTel's, using the Datadog conventions."""
    if method is not None:
        span._set_attribute(http.METHOD, method)

    if url is not None:
        url = _sanitized_url(url)
        _set_url_tag(integration_config, span, url, query)

    if target_host is not None:
        span._set_attribute(net.TARGET_HOST, target_host)

    if server_address is not None:
        span._set_attribute(net.SERVER_ADDRESS, server_address)

    if status_code is not None:
        try:
            int_status_code = int(status_code)
        except (TypeError, ValueError):
            log.debug("failed to convert http status code %r to int", status_code)
        else:
            span._set_attribute(http.STATUS_CODE, str(status_code))
            if config._http_server.is_error_code(int_status_code):
                span.error = 1

    if query is not None and integration_config.trace_query_string:
        span._set_attribute(http.QUERY_STRING, query)

    if request_headers:
        user_agent = _get_request_header_user_agent(request_headers, headers_are_case_sensitive)
        if user_agent:
            span._set_attribute(http.USER_AGENT, user_agent)


def _set_http_meta_otel(
    otel_http: OTelHTTPSpanAttributes,
    method: Optional[str],
    url: Optional[str],
    raw_uri: Optional[str],
    target_host: Optional[str],
    server_address: Optional[str],
    status_code: Optional[Union[int, str]],
    query: Optional[str],
    query_tagged_separately: bool,
    request_headers: Optional[Mapping[str, str]],
    headers_are_case_sensitive: bool,
    route: Optional[str],
) -> None:
    """Set the tags whose names or semantics differ between conventions, using the OTel conventions."""
    otel_http.set_method(method)
    otel_http.set_url(
        url,
        query=query,
        raw_uri=raw_uri,
        server_address=server_address,
        fallback_server_address=target_host,
        query_tagged_separately=query_tagged_separately,
    )
    otel_http.set_status_code(status_code)
    if request_headers:
        otel_http.set_user_agent(_get_request_header_user_agent(request_headers, headers_are_case_sensitive))
    otel_http.set_resource(route)
