"""
This module contains utility functions for writing ddtrace integrations.
"""
from collections import deque
import re
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import Iterator
from typing import Optional
from typing import TYPE_CHECKING
from typing import Tuple

from ddtrace import Pin
from ddtrace import config
from ddtrace.ext import http
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import normalize_header_name
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


@cached()
def _normalized_header_name(header_name):
    # type: (str) -> str
    return NORMALIZE_PATTERN.sub("_", normalize_header_name(header_name))


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
        tag_name = integration_config._header_tag_name(header_name)
        if tag_name is None:
            continue
        # An empty tag defaults to a http.<request or response>.headers.<header name> tag
        span.set_tag(tag_name or _normalize_tag_name(request_or_response, header_name), header_value)


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
    """Returns the service name for an integration which is internal
    to the application. Internal meaning that the work belongs to the
    user's application. Eg. Web framework, sqlalchemy, web servers.

    For internal integrations we prioritize overrides, then global defaults and
    lastly the default provided by the integration.
    """
    int_config = int_config or {}

    # Pin has top priority since it is user defined in code
    if pin and pin.service:
        return pin.service

    # Config is next since it is also configured via code
    # Note that both service and service_name are used by
    # integrations.
    if "service" in int_config and int_config.service is not None:
        return int_config.service
    if "service_name" in int_config and int_config.service_name is not None:
        return int_config.service_name

    global_service = int_config.global_config._get_service()
    if global_service:
        return global_service

    if "_default_service" in int_config and int_config._default_service is not None:
        return int_config._default_service

    return default


def ext_service(pin, int_config, default=None):
    """Returns the service name for an integration which is external
    to the application. External meaning that the integration generates
    spans wrapping code that is outside the scope of the user's application. Eg. A database, RPC, cache, etc.
    """
    int_config = int_config or {}

    if pin and pin.service:
        return pin.service

    if "service" in int_config and int_config.service is not None:
        return int_config.service
    if "service_name" in int_config and int_config.service_name is not None:
        return int_config.service_name

    if "_default_service" in int_config and int_config._default_service is not None:
        return int_config._default_service

    # A default is required since it's an external service.
    return default


def set_http_meta(
    span,
    integration_config,
    method=None,
    url=None,
    status_code=None,
    status_msg=None,
    query=None,
    request_headers=None,
    response_headers=None,
    retries_remain=None,
):
    if method is not None:
        span._set_str_tag(http.METHOD, method)

    if url is not None:
        span._set_str_tag(http.URL, url if integration_config.trace_query_string else strip_query_string(url))

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

    if request_headers is not None and integration_config.is_header_tracing_configured:
        _store_request_headers(dict(request_headers), span, integration_config)

    if response_headers is not None and integration_config.is_header_tracing_configured:
        _store_response_headers(dict(response_headers), span, integration_config)

    if retries_remain is not None:
        span._set_str_tag(http.RETRIES_REMAIN, str(retries_remain))


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
        if context.trace_id:
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
