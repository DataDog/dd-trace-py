import os
import wrapt
import falcon

from ddtrace import tracer

from .middleware import TraceMiddleware


def patch():
    """
    Patch falcon.API to include contrib.falcon.TraceMiddleware
    by default
    """
    if getattr(falcon, '_datadog_patch', False):
        return

    setattr(falcon, '_datadog_patch', True)
    wrapt.wrap_function_wrapper('falcon', 'API.__init__', traced_init)

def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop('middleware', [])
    service = os.environ.get('DATADOG_SERVICE_NAME') or 'falcon'

    mw.insert(0, TraceMiddleware(tracer, service))
    kwargs['middleware'] = mw

    wrapped(*args, **kwargs)
