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
    # _w = wrapt.wrap_function_wrapper
    # _w('flask.Flask', 'full_dispatch_request', traced_full_dispatch_request)


class TracedFlask(flask.Flask):

    def __init__(self, *args, **kwargs):
        super(TracedFlask, self).__init__(*args, **kwargs)
        service = os.environ.get("DATADOG_SERVICE_NAME") or "flask"

        traced_app = TraceMiddleware(self, tracer, service=service)

        from flask import signals

        # traced_app.app has signals
        assert len(list(signals.request_started.receivers_for(traced_app.app))) > 0

        assert traced_app.app is self

        assert len(list(signals.request_started.receivers_for(self))) > 0
        # Signals are registered here


# def traced_full_dispatch_request(wrapped, instance, args, kwargs):
#     instance.try_trigger_before_first_request_functions()
#     try:
#         request_started.send(self)
#         rv = instance.preprocess_request()
#         if rv is None:
#             rv = instance.dispatch_request()
#     except Exception as e:
#         rv = instance.handle_user_exception(e)
#     return instance.finalize_request(rv)
#
#
# def full_dispatch_request(self):
#     """Dispatches the request and on top of that performs request
#     pre and postprocessing as well as HTTP exception catching and
#     error handling.
#     .. versionadded:: 0.7
#     """
#     self.try_trigger_before_first_request_functions()
#     try:
#         request_started.send(self)
#         rv = self.preprocess_request()
#         if rv is None:
#             rv = self.dispatch_request()
#     except Exception as e:
#         rv = self.handle_user_exception(e)
#     return self.finalize_request(rv)
