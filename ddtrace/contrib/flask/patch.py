import os

from .middleware import TraceMiddleware
from ddtrace import tracer

import flask

import wrapt

def patch():
    """Patch the instrumented Flask object
    """
    if getattr(flask, '_datadog_patch', False):
        return

    setattr(flask, '_datadog_patch', True)
    wrapt.wrap_function_wrapper('flask', 'Flask.__init__', traced_init)

def traced_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)

    service = os.environ.get("DATADOG_SERVICE_NAME") or "flask"
    traced_app = TraceMiddleware(instance, tracer, service=service)

    # Keep a reference to our blinker signal receivers to prevent them from being garbage collected
    setattr(instance, '_datadog_receivers', traced_app._receivers)
