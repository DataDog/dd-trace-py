"""
This module contains utility functions for writing ddtrace integrations.
"""
from ddtrace import Pin
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
