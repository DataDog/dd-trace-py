import os
import flask
import wrapt

from ddtrace import tracer

from .middleware import TraceMiddleware


def patch():
    """Patch the instrumented Flask object
    """
    if getattr(flask, '_datadog_patch', False):
        return

    setattr(flask, '_datadog_patch', True)
    wrapt.wrap_function_wrapper('flask', 'Flask.__init__', traced_init)


def traced_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)

    service = os.environ.get('DATADOG_SERVICE_NAME') or 'flask'
    TraceMiddleware(instance, tracer, service=service)
