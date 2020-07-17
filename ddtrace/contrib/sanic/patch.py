from ddtrace import Pin, config
from ddtrace import tracer
from ddtrace.ext import SpanTypes, http
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.utils.wrappers import unwrap as _u, iswrapped
from ddtrace.utils.importlib import func_name

import sanic

from ...internal.logger import get_logger
from ...utils.formats import asbool, get_env

log = get_logger(__name__)

config._add("sanic", dict(service=config._get_service(default="sanic"), distributed_tracing=True))


def patch():
    """Patch the instrumented methods.
    """
    if getattr(sanic, "__datadog_patch", False):
        return
    setattr(sanic, "__datadog_patch", True)
    _w("sanic", "Sanic.handle_request", patch_handle_request)
    _w("sanic", "Sanic.register_middleware", patch_register_middleware)


def unpatch():
    """Unpatch the instrumented methods.
    """
    _u(sanic.Sanic, "handle_request")


def patch_handle_request(wrapped, instance, args, kwargs):
    """Wrapper for Sanic.handle_request"""
    request = args[0]
    resource = "{} {}".format(request.method, request.path)

    with tracer.trace("sanic.request", service=config.sanic.service, resource=resource, span_type=SpanTypes.WEB):
        return wrapped(*args, **kwargs)


def patch_register_middleware(wrapped, instance, args, kwargs):
    # TODO: wrap middleware
    wrapped(*args, **kwargs)
