import os
import wrapt
import pylons.wsgiapp

import ddtrace
from ddtrace import Pin

from .middleware import PylonsTraceMiddleware


_PylonsApp = pylons.wsgiapp.PylonsApp


def patch():
    """Instrument Pylons applications"""
    if getattr(pylons.wsgiapp, '_datadog_patch', False):
        return

    setattr(pylons.wsgiapp, '_datadog_patch', True)
    setattr(pylons.wsgiapp, 'PylonsApp', TracedPylonsApp)


def unpatch():
    """Disable Pylons tracing"""
    if not getattr(pylons.wsgiapp, '_datadog_patch', False):
        return
    setattr(pylons.wsgiapp, '_datadog_patch', False)

    setattr(pylons.wsgiapp, 'PylonsApp', _PylonsApp)


class TracedPylonsApp(wrapt.ObjectProxy):
    def __init__(self, *args, **kwargs):
        app = _PylonsApp(*args, **kwargs)
        super(TracedPylonsApp, self).__init__(app)

        tracer = ddtrace.tracer

        # set tracing options and create the TraceMiddleware
        service = os.environ.get('DATADOG_SERVICE_NAME', 'pylons')
        distributed_tracing = os.environ.get('DATADOG_PYLONS_DISTRIBUTED_TRACING', False)
        Pin(service=service, tracer=tracer).onto(app)
        self.traced_app = PylonsTraceMiddleware(app, tracer, service=service, distributed_tracing=distributed_tracing)

    def __call__(self, *args, **kwargs):
        return self.traced_app.__call__(*args, **kwargs)
