import os
import wrapt
import falcon

import ddtrace

from .middleware import TraceMiddleware
from ...utils.formats import asbool


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
    distributed_tracing = asbool(os.environ.get(
        'DATADOG_FALCON_DISTRIBUTED_TRACING')) or False
    # It appears that the only way to install middleware is via the initializer
    # so it's sufficient to check the middleware for our middleware to
    # ensure idempotence.
    # If the tracing middleware is already included then defer to that
    if not _find_dd_middleware(mw):
        mw.insert(0, TraceMiddleware(ddtrace.tracer, service, distributed_tracing))
    kwargs['middleware'] = mw
    wrapped(*args, **kwargs)


def _find_dd_middleware(middlewares):
    """
    Returns whether or not the Datadog middleware is in a list of falcon
    middlewares.
    """
    for mw in middlewares:
        if isinstance(mw, TraceMiddleware):
            return True
    return False
