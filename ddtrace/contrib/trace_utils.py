"""
This module contains utility functions for writing ddtrace integrations.
"""
from ddtrace import Pin, config
from ddtrace.ext import http
import ddtrace.http
from ddtrace.internal.logger import get_logger
import ddtrace.utils.wrappers
from ddtrace.vendor import wrapt
from ..compat import stringify

log = get_logger(__name__)

wrap = wrapt.wrap_function_wrapper
unwrap = ddtrace.utils.wrappers.unwrap
iswrapped = ddtrace.utils.wrappers.iswrapped

store_request_headers = ddtrace.http.store_request_headers
store_response_headers = ddtrace.http.store_response_headers


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


def int_service(pin, config, default=None):
    """Returns the service name for an integration which is internal
    to the application. Internal meaning that the work belongs to the
    user's application. Eg. Web framework, sqlalchemy, web servers.

    For internal integrations we prioritize overrides, then global defaults and
    lastly the default provided by the integration.
    """
    config = config or {}

    # Pin has top priority since it is user defined in code
    if pin and pin.service:
        return pin.service

    # Config is next since it is also configured via code
    # Note that both service and service_name are used by
    # integrations.
    if "service" in config and config.service is not None:
        return config.service
    if "service_name" in config and config.service_name is not None:
        return config.service_name

    global_service = config.global_config._get_service()
    if global_service:
        return global_service

    if "_default_service" in config and config._default_service is not None:
        return config._default_service

    return default


def ext_service(pin, config, default=None):
    """Returns the service name for an integration which is external
    to the application. External meaning that the integration generates
    spans wrapping code that is outside the scope of the user's application. Eg. A database, RPC, cache, etc.
    """
    config = config or {}

    if pin and pin.service:
        return pin.service

    if "service" in config and config.service is not None:
        return config.service
    if "service_name" in config and config.service_name is not None:
        return config.service_name

    if "_default_service" in config and config._default_service is not None:
        return config._default_service

    # A default is required since it's an external service.
    return default


def get_error_codes():
    error_codes = []
    try:
        error_str = config.http_server.error_statuses
    except AttributeError:
        error_str = None
    if error_str is None:
        return [[500, 599]]
    error_ranges = error_str.split(",")
    for error_range in error_ranges:
        values = error_range.split("-")
        min_code = int(values[0])
        if len(values) == 2:
            max_code = int(values[1])
        else:
            max_code = min_code
        if min_code > max_code:
            tmp = min_code
            min_code = max_code
            max_code = tmp
        error_codes.append([min_code, max_code])
    return error_codes


def set_http_meta(span, integration_config, method=None, url=None, status_code=None, query_params=None, headers=None):
    if method is not None:
        span.meta[http.METHOD] = method

    if url is not None:
        span.meta[http.URL] = stringify(url)

    if status_code is not None:
        span.meta[http.STATUS_CODE] = str(status_code)
        error_codes = get_error_codes()
        for error_code in error_codes:
            if error_code[0] <= int(status_code) <= error_code[1]:
                span.error = 1

    if query_params is not None and integration_config.trace_query_string:
        span.meta[http.QUERY_STRING] = query_params

    if headers is not None:
        store_request_headers(headers, span, integration_config)
