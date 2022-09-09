"""
This module contains utility functions for writing ddtrace integrations.
"""
from collections import deque
import ipaddress
import os
import re
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import Iterator
from typing import List
from typing import Mapping
from typing import Optional
from typing import TYPE_CHECKING
from typing import Tuple
from typing import Union
from typing import cast

from ddtrace import Pin
from ddtrace import config
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal import _context
from ddtrace.internal.compat import six
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.http import normalize_header_name
from ddtrace.internal.utils.http import redact_url
from ddtrace.internal.utils.http import strip_query_string
import ddtrace.internal.utils.wrappers
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor import wrapt


if TYPE_CHECKING:
    from ddtrace import Span
    from ddtrace import Tracer
    from ddtrace.settings import IntegrationConfig


log = get_logger(__name__)

wrap = wrapt.wrap_function_wrapper
unwrap = ddtrace.internal.utils.wrappers.unwrap
iswrapped = ddtrace.internal.utils.wrappers.iswrapped

REQUEST = "request"
RESPONSE = "response"

# Tag normalization based on: https://docs.datadoghq.com/tagging/#defining-tags
# With the exception of '.' in header names which are replaced with '_' to avoid
# starting a "new object" on the UI.
NORMALIZE_PATTERN = re.compile(r"([^a-z0-9_\-:/]){1}")

# Possible User Agent header.
USER_AGENT_PATTERNS = ("http-user-agent", "user-agent")

IP_PATTERNS = (
    "x-forwarded-for",
    "x-real-ip",
    "client-ip",
    "x-forwarded",
    "x-cluster-client-ip",
    "forwarded-for",
    "forwarded",
    "via",
    "true-client-ip",
)


@cached()
def _normalized_header_name(header_name):
    # type: (str) -> str
    return NORMALIZE_PATTERN.sub("_", normalize_header_name(header_name))


def _get_header_value_case_insensitive(headers, keyname):
    # type: (Mapping[str, str], str) -> Optional[str]
    """
    Get a header in a case insensitive way. This function is meant for frameworks
    like Django < 2.2 that don't store the headers in a case insensitive mapping.
    """
    # just in case we are lucky
    shortcut_value = headers.get(keyname)
    if shortcut_value is not None:
        return shortcut_value

    for key, value in six.iteritems(headers):
        if key.lower().replace("_", "-") == keyname:
            return value

    return None


def _normalize_tag_name(request_or_response, header_name):
    # type: (str, str) -> str
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


def _store_headers(headers, span, integration_config, request_or_response):
    # type: (Dict[str, str], Span, IntegrationConfig, str) -> None
    """
    :param headers: A dict of http headers to be stored in the span
    :type headers: dict or list
    :param span: The Span instance where tags will be stored
    :type span: ddtrace.span.Span
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
        """config._header_tag_name gets an element of the dictionary in config.http._header_tags
        which gets the value from DD_TRACE_HEADER_TAGS environment variable."""
        tag_name = integration_config._header_tag_name(header_name)
        if tag_name is None:
            continue
        # An empty tag defaults to a http.<request or response>.headers.<header name> tag
        span.set_tag(tag_name or _normalize_tag_name(request_or_response, header_name), header_value)


def _get_request_header_user_agent(headers, headers_are_case_sensitive=False):
    # type: (Mapping[str, str], bool) -> str
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


# Used to cache the last header used for the cache. From the same server/framework
# usually the same header will be used on further requests, so we use this to check
# only it.
_USED_IP_HEADER = ""


def _get_request_header_client_ip(span, headers, peer_ip=None, headers_are_case_sensitive=False):
    # type: (Span, Mapping[str, str], Optional[str], bool) -> str
    global _USED_IP_HEADER

    if asbool(os.getenv("DD_TRACE_CLIENT_IP_HEADER_DISABLED", default=False)):
        return ""

    def get_header_value(key):  # type: (str) -> Optional[str]
        if not headers_are_case_sensitive:
            return headers.get(key)

        return _get_header_value_case_insensitive(headers, key)

    ip_header_value = ""
    user_configured_ip_header = os.getenv("DD_TRACE_CLIENT_IP_HEADER", None)
    if user_configured_ip_header:
        # Used selected the header to use to get the IP
        ip_header_value = headers.get(user_configured_ip_header)
        if not ip_header_value:
            log.debug("DD_TRACE_CLIENT_IP_HEADER configured but '%s' header missing", user_configured_ip_header)
            return ""

        try:
            _ = ipaddress.ip_address(six.text_type(ip_header_value))
        except ValueError:
            log.debug("Invalid IP address from configured %s header: %s", user_configured_ip_header, ip_header_value)
            return ""

    else:
        # No configured IP header, go through the list defined in:
        # https://datadoghq.atlassian.net/wiki/spaces/APS/pages/2118779066/Client+IP+addresses+resolution
        if _USED_IP_HEADER:
            ip_header_value = get_header_value(_USED_IP_HEADER)

        # For some reason request uses other header or the specified header is empty, do the
        # search
        if not ip_header_value:
            for ip_header in IP_PATTERNS:
                tmp_ip_header_value = get_header_value(ip_header)
                if tmp_ip_header_value:
                    ip_header_value = tmp_ip_header_value
                    _USED_IP_HEADER = ip_header
                    break

    # At this point, we have one IP header, check its value and retrieve the first public IP
    private_ip = ""

    if ip_header_value:
        ip_list = ip_header_value.split(",")
        for ip in ip_list:
            ip = ip.strip()
            if not ip:
                continue

            try:
                ip_obj = ipaddress.ip_address(six.text_type(ip))
            except ValueError:
                continue

            if not ip_obj.is_private:  # is_global is Python3+ only
                return ip
            elif not private_ip and not ip_obj.is_loopback:
                private_ip = ip
            # else: it's private, but we already have one: continue in case we find a public one

    # So we have none or maybe one private ip, check the peer ip in case it's public and if not
    # return either the private_ip from the headers (if we have one) or the peer private ip
    if peer_ip:
        try:
            peer_ip_obj = ipaddress.ip_address(six.text_type(peer_ip))
            if not peer_ip_obj.is_loopback:
                # If we have a private_ip and the peer_ip is not public, prefer the one we have
                if private_ip:
                    if peer_ip_obj.is_loopback or peer_ip_obj.is_private:
                        return private_ip
                # peer_ip is public or we dont have a private_ip to we return the private peer_ip
                return peer_ip
        except ValueError:
            pass

    return private_ip


def _store_request_headers(headers, span, integration_config):
    # type: (Dict[str, str], Span, IntegrationConfig) -> None
    """
    Store request headers as a span's tags
    :param headers: All the request's http headers, will be filtered through the whitelist
    :type headers: dict or list
    :param span: The Span instance where tags will be stored
    :type span: ddtrace.Span
    :param integration_config: An integration specific config object.
    :type integration_config: ddtrace.settings.IntegrationConfig
    """
    _store_headers(headers, span, integration_config, REQUEST)


def _store_response_headers(headers, span, integration_config):
    # type: (Dict[str, str], Span, IntegrationConfig) -> None
    """
    Store response headers as a span's tags
    :param headers: All the response's http headers, will be filtered through the whitelist
    :type headers: dict or list
    :param span: The Span instance where tags will be stored
    :type span: ddtrace.Span
    :param integration_config: An integration specific config object.
    :type integration_config: ddtrace.settings.IntegrationConfig
    """
    _store_headers(headers, span, integration_config, RESPONSE)


def with_traced_module(func):
    """Helper for providing tracing essentials (module and pin) for tracing
    wrappers.

    This helper enables tracing wrappers to dynamically be disabled when the
    corresponding pin is disabled.

    Usage::

        @with_traced_module
        def my_traced_wrapper(django, pin, func, instance, args, kwargs):
            # Do tracing stuff
            pass

        def patch():
            import django
            wrap(django.somefunc, my_traced_wrapper(django))
    """

    def with_mod(mod):
        def wrapper(wrapped, instance, args, kwargs):
            pin = Pin._find(instance, mod)
            if pin and not pin.enabled():
                return wrapped(*args, **kwargs)
            elif not pin:
                log.debug("Pin not found for traced method %r", wrapped)
                return wrapped(*args, **kwargs)
            return func(mod, pin, wrapped, instance, args, kwargs)

        return wrapper

    return with_mod


def distributed_tracing_enabled(int_config, default=False):
    # type: (IntegrationConfig, bool) -> bool
    """Returns whether distributed tracing is enabled for this integration config"""
    if "distributed_tracing_enabled" in int_config and int_config.distributed_tracing_enabled is not None:
        return int_config.distributed_tracing_enabled
    elif "distributed_tracing" in int_config and int_config.distributed_tracing is not None:
        return int_config.distributed_tracing
    return default


def int_service(pin, int_config, default=None):
    # type: (Optional[Pin], IntegrationConfig, Optional[str]) -> Optional[str]
    """Returns the service name for an integration which is internal
    to the application. Internal meaning that the work belongs to the
    user's application. Eg. Web framework, sqlalchemy, web servers.

    For internal integrations we prioritize overrides, then global defaults and
    lastly the default provided by the integration.
    """
    # Pin has top priority since it is user defined in code
    if pin is not None and pin.service:
        return pin.service

    # Config is next since it is also configured via code
    # Note that both service and service_name are used by
    # integrations.
    if "service" in int_config and int_config.service is not None:
        return cast(str, int_config.service)
    if "service_name" in int_config and int_config.service_name is not None:
        return cast(str, int_config.service_name)

    global_service = int_config.global_config._get_service()
    if global_service:
        return cast(str, global_service)

    if "_default_service" in int_config and int_config._default_service is not None:
        return cast(str, int_config._default_service)

    return default


def ext_service(pin, int_config, default=None):
    # type: (Optional[Pin], IntegrationConfig, Optional[str]) -> Optional[str]
    """Returns the service name for an integration which is external
    to the application. External meaning that the integration generates
    spans wrapping code that is outside the scope of the user's application. Eg. A database, RPC, cache, etc.
    """
    if pin is not None and pin.service:
        return pin.service

    if "service" in int_config and int_config.service is not None:
        return cast(str, int_config.service)
    if "service_name" in int_config and int_config.service_name is not None:
        return cast(str, int_config.service_name)

    if "_default_service" in int_config and int_config._default_service is not None:
        return cast(str, int_config._default_service)

    # A default is required since it's an external service.
    return default


def set_http_meta(
    span,  # type: Span
    integration_config,  # type: IntegrationConfig
    method=None,  # type: Optional[str]
    url=None,  # type: Optional[str]
    status_code=None,  # type: Optional[Union[int, str]]
    status_msg=None,  # type: Optional[str]
    query=None,  # type: Optional[str]
    parsed_query=None,  # type: Optional[Mapping[str, str]]
    request_headers=None,  # type: Optional[Mapping[str, str]]
    response_headers=None,  # type: Optional[Mapping[str, str]]
    retries_remain=None,  # type: Optional[Union[int, str]]
    raw_uri=None,  # type: Optional[str]
    request_cookies=None,  # type: Optional[Dict[str, str]]
    request_path_params=None,  # type: Optional[Dict[str, str]]
    request_body=None,  # type: Optional[Union[str, Dict[str, List[str]]]]
    peer_ip=None,  # type: Optional[str]
    headers_are_case_sensitive=False,  # type: bool
):
    # type: (...) -> None
    """
    Set HTTP metas on the span

    :param method: the HTTP method
    :param url: the HTTP URL
    :param status_code: the HTTP status code
    :param status_msg: the HTTP status message
    :param query: the HTTP query part of the URI as a string
    :param parsed_query: the HTTP query part of the URI as parsed by the framework and forwarded to the user code
    :param request_headers: the HTTP request headers
    :param response_headers: the HTTP response headers
    :param raw_uri: the full raw HTTP URI (including ports and query)
    :param request_cookies: the HTTP request cookies as a dict
    :param request_path_params: the parameters of the HTTP URL as set by the framework: /posts/<id:int> would give us
         { "id": <int_value> }
    """
    if method is not None:
        span._set_str_tag(http.METHOD, method)

    if url is not None:
        if integration_config.trace_query_string and not config.global_trace_query_string_disabled:
            span._set_str_tag(http.URL, redact_url(url, config._obfuscation_query_string_pattern, query))
        else:
            span._set_str_tag(http.URL, strip_query_string(url))

    if status_code is not None:
        try:
            int_status_code = int(status_code)
        except (TypeError, ValueError):
            log.debug("failed to convert http status code %r to int", status_code)
        else:
            span._set_str_tag(http.STATUS_CODE, str(status_code))
            if config.http_server.is_error_code(int_status_code):
                span.error = 1

    if status_msg is not None:
        span._set_str_tag(http.STATUS_MSG, status_msg)

    if query is not None and integration_config.trace_query_string:
        span._set_str_tag(http.QUERY_STRING, query)

    ip = None
    if request_headers:
        user_agent = _get_request_header_user_agent(request_headers, headers_are_case_sensitive)
        if user_agent:
            span.set_tag(http.USER_AGENT, user_agent)

        if integration_config.is_header_tracing_configured:
            """We should store both http.<request_or_response>.headers.<header_name> and http.<key>. The last one
            is the DD standardized tag for user-agent"""
            _store_request_headers(dict(request_headers), span, integration_config)

        ip = _get_request_header_client_ip(span, request_headers, peer_ip, headers_are_case_sensitive)
        if ip:
            span.set_tag(http.CLIENT_IP, ip)
            if span._meta:
                span._meta["network.client.ip"] = ip
                span._meta["actor.ip"] = ip

    if response_headers is not None and integration_config.is_header_tracing_configured:
        _store_response_headers(dict(response_headers), span, integration_config)

    if retries_remain is not None:
        span._set_str_tag(http.RETRIES_REMAIN, str(retries_remain))

    if config._appsec_enabled:
        status_code = str(status_code) if status_code is not None else None

        _context.set_items(
            {
                k: v
                for k, v in [
                    ("http.request.uri", raw_uri),
                    ("http.request.method", method),
                    ("http.request.cookies", request_cookies),
                    ("http.request.query", parsed_query),
                    ("http.request.headers", request_headers),
                    ("http.response.headers", response_headers),
                    ("http.response.status", status_code),
                    ("http.request.path_params", request_path_params),
                    ("http.request.body", request_body),
                    ("http.request.remote_ip", ip),
                ]
                if v is not None
            },
            span=span,
        )


def activate_distributed_headers(tracer, int_config=None, request_headers=None, override=None):
    # type: (Tracer, Optional[IntegrationConfig], Optional[Dict[str, str]], Optional[bool]) -> None
    """
    Helper for activating a distributed trace headers' context if enabled in integration config.
    int_config will be used to check if distributed trace headers context will be activated, but
    override will override whatever value is set in int_config if passed any value other than None.
    """
    if override is False:
        return None

    if override or (int_config and distributed_tracing_enabled(int_config)):
        context = HTTPPropagator.extract(request_headers)

        # Only need to activate the new context if something was propagated
        if not context.trace_id:
            return None

        # Do not reactivate a context with the same trace id
        # DEV: An example could be nested web frameworks, when one layer already
        #      parsed request headers and activated them.
        #
        # Example::
        #
        #     app = Flask(__name__)  # Traced via Flask instrumentation
        #     app = DDWSGIMiddleware(app)  # Extra layer on top for WSGI
        current_context = tracer.current_trace_context()
        if current_context and current_context.trace_id == context.trace_id:
            log.debug(
                "will not activate extracted Context(trace_id=%r, span_id=%r), a context with that trace id is already active",  # noqa: E501
                context.trace_id,
                context.span_id,
            )
            return None

        # We have parsed a trace id from headers, and we do not already
        # have a context with the same trace id active
        tracer.context_provider.activate(context)


def _flatten(
    obj,  # type: Any
    sep=".",  # type: str
    prefix="",  # type: str
    exclude_policy=None,  # type: Optional[Callable[[str], bool]]
):
    # type: (...) -> Generator[Tuple[str, Any], None, None]
    s = deque()  # type: ignore
    s.append((prefix, obj))
    while s:
        p, v = s.pop()
        if exclude_policy is not None and exclude_policy(p):
            continue
        if isinstance(v, dict):
            s.extend((sep.join((p, k)) if p else k, v) for k, v in v.items())
        else:
            yield p, v


def set_flattened_tags(
    span,  # type: Span
    items,  # type: Iterator[Tuple[str, Any]]
    sep=".",  # type: str
    exclude_policy=None,  # type: Optional[Callable[[str], bool]]
    processor=None,  # type: Optional[Callable[[Any], Any]]
):
    # type: (...) -> None
    for prefix, value in items:
        for tag, v in _flatten(value, sep, prefix, exclude_policy):
            span.set_tag(tag, processor(v) if processor is not None else v)


def set_user(tracer, user_id, name=None, email=None, scope=None, role=None, session_id=None, propagate=False):
    # type: (Tracer, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], bool) -> None
    """Set user tags.
    https://docs.datadoghq.com/logs/log_configuration/attributes_naming_convention/#user-related-attributes
    https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/?tab=set_tag&code-lang=python
    """
    span = tracer.current_root_span()
    if span:
        # Required unique identifier of the user
        span.set_tag(user.ID, user_id)
        if propagate:
            span.context.dd_user_id = user_id

        # All other fields are optional
        if name:
            span.set_tag(user.NAME, name)
        if email:
            span.set_tag(user.EMAIL, email)
        if scope:
            span.set_tag(user.SCOPE, scope)
        if role:
            span.set_tag(user.ROLE, role)
        if session_id:
            span.set_tag(user.SESSION_ID, session_id)
    else:
        log.warning(
            "No root span in the current execution. Skipping set_user tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )
