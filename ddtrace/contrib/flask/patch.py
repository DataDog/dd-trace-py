import os

from .middleware import TraceMiddleware
from ddtrace import tracer

import flask


def patch():
    """Patch the instrumented Flask object
    """
    if getattr(flask, '_datadog_patch', False):
        return

    setattr(flask, '_datadog_patch', True)
    setattr(flask, 'Flask', TracedFlask)

class TracedFlask(flask.Flask):

    def __init__(self, *args, **kwargs):
        super(TracedFlask, self).__init__(*args, **kwargs)
        service = os.environ.get("DATADOG_SERVICE_NAME") or "flask"
        traced_app = TraceMiddleware(self, tracer, service=service)

        # Keep a reference to our blinker signal receivers to prevent them from being garbage collected
        setattr(self, '_datadog_receivers', traced_app._receivers)
