"""
This module contains public utility functions for writing ddtrace integrations.
"""
from typing import Dict
from typing import Optional
from typing import TYPE_CHECKING

from ddtrace import Pin
from ddtrace.internal.logger import get_logger
import ddtrace.internal.utils.wrappers
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor import wrapt


if TYPE_CHECKING:
    from ddtrace import Tracer
    from ddtrace.settings import IntegrationConfig


log = get_logger(__name__)

wrap = wrapt.wrap_function_wrapper
unwrap = ddtrace.internal.utils.wrappers.unwrap
iswrapped = ddtrace.internal.utils.wrappers.iswrapped


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
