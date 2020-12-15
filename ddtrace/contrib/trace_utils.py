"""
This module contains utility functions for writing ddtrace integrations.
"""
from ddtrace import Pin, config
from ddtrace.ext import http
import ddtrace.http
from ddtrace.internal.logger import get_logger
import ddtrace.utils.wrappers
from ddtrace.vendor import wrapt

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


def is_error_code(status_code):
    """Returns a boolean representing whether or not a status code is an error code.
    Error status codes by default are 500-599.
    You may also enable custom error codes::

        from ddtrace import config
        config.http_server.error_statuses = '401-404,419'

    Ranges and singular error codes are permitted and can be separated using commas.
    """

    if not isinstance(config.http_server.error_statuses, str):
        log.debug("Error statuses in config are not a string")
        return False

    error_str = config.http_server.error_statuses.strip()
    error_ranges = error_str.split(",")
    for error_range in error_ranges:
        values = error_range.split("-")
        try:
            values = [int(v) for v in values]
        except ValueError:
            log.debug("Invalid status code range for %s", error_range)
            continue
        if min(values) <= int(status_code) <= max(values):
            return True
    return False


def set_http_meta(
    span,
    integration_config,
    method=None,
    url=None,
    status_code=None,
    query=None,
    request_headers=None,
    response_headers=None,
):
    if method is not None:
        span._set_str_tag(http.METHOD, method)

    if url is not None:
        span._set_str_tag(http.URL, url)

    if status_code is not None:
        span._set_str_tag(http.STATUS_CODE, status_code)
        if is_error_code(status_code):
            span.error = 1

    if query is not None and integration_config.trace_query_string:
        span._set_str_tag(http.QUERY_STRING, query)

    if request_headers is not None:
        store_request_headers(dict(request_headers), span, integration_config)

    if response_headers is not None:
        store_response_headers(dict(response_headers), span, integration_config)
