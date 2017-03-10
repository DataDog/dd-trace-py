import os

from .middleware import TraceMiddleware
from ddtrace import tracer

import falcon


def patch():
    """
    Patch falcon.API to include contrib.falcon.TraceMiddleware
    by default
    """
    if getattr(falcon, '_datadog_patch', False):
        return

    setattr(falcon, '_datadog_patch', True)
    setattr(falcon, 'API', TracedAPI)


class TracedAPI(falcon.API):

    def __init__(self, *args, **kwargs):
        mw = kwargs.pop("middleware", [])
        service = os.environ.get("DATADOG_SERVICE_NAME") or "falcon"

        mw.insert(0, TraceMiddleware(tracer, service))
        kwargs["middleware"] = mw

        super(TracedAPI, self).__init__(*args, **kwargs)
