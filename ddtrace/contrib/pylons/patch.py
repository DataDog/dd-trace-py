import os

from .middleware import PylonsTraceMiddleware

from ddtrace import tracer, Pin

import wrapt

import pylons.wsgiapp

def patch():
    """Patch the instrumented Flask object
    """
    if getattr(pylons.wsgiapp, '_datadog_patch', False):
        return

    setattr(pylons.wsgiapp, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('pylons.wsgiapp', 'PylonsApp.__init__', traced_init)

def traced_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)

    service = os.environ.get("DATADOG_SERVICE_NAME") or "pylons"
    Pin(service=service, tracer=tracer).onto(instance)
    PylonsTraceMiddleware(instance, tracer, service=service)
