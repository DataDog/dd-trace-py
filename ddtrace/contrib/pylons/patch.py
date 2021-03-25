import pylons.wsgiapp

from ddtrace import Pin
from ddtrace import config
from ddtrace import tracer
from ddtrace.vendor import wrapt

from ...utils.formats import asbool
from ...utils.formats import get_env
from ...utils.wrappers import unwrap as _u
from .middleware import PylonsTraceMiddleware


def patch():
    """Instrument Pylons applications"""
    if getattr(pylons.wsgiapp, "_datadog_patch", False):
        return

    setattr(pylons.wsgiapp, "_datadog_patch", True)
    wrapt.wrap_function_wrapper("pylons.wsgiapp", "PylonsApp.__init__", traced_init)


def unpatch():
    """Disable Pylons tracing"""
    if not getattr(pylons.wsgiapp, "__datadog_patch", False):
        return
    setattr(pylons.wsgiapp, "__datadog_patch", False)

    _u(pylons.wsgiapp.PylonsApp, "__init__")


def traced_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)

    # set tracing options and create the TraceMiddleware
    service = config._get_service(default="pylons")
    distributed_tracing = asbool(get_env("pylons", "distributed_tracing", default=True))
    Pin(service=service, tracer=tracer).onto(instance)
    traced_app = PylonsTraceMiddleware(instance, tracer, service=service, distributed_tracing=distributed_tracing)

    # re-order the middleware stack so that the first middleware is ours
    traced_app.app = instance.app
    instance.app = traced_app
