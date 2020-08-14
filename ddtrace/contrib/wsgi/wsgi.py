import sys

from ddtrace.vendor import six

import ddtrace
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger

from .. import trace_utils


log = get_logger(__name__)

config._add("wsgi", dict(_default_service="wsgi"))


class DDTraceWrite(object):
    def __init__(self, write, tracer):
        self._write = write
        self._tracer = tracer

    def __call__(self, data):
        self._write(data)


class DDWSGIMiddleware(object):
    """WSGI middleware providing tracing around an application.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    """

    def __init__(self, application, tracer=None):
        self.app = application
        self.tracer = tracer or ddtrace.tracer

    def __call__(self, environ, start_response):
        def intercept_start_response(status, response_headers, exc_info=None):
            with self.tracer.trace(
                "wsgi.start_response",
                service=trace_utils.int_service(None, config.wsgi),
                span_type=SpanTypes.WEB,
            ):
                write = start_response(status, response_headers, exc_info)
            return DDTraceWrite(write, self.tracer)

        with self.tracer.trace(
            "wsgi.request",
            service=trace_utils.int_service(None, config.wsgi),
            span_type=SpanTypes.WEB,
        ) as span:
            with self.tracer.trace("wsgi.application"):
                result = self.app(environ, intercept_start_response)

            with self.tracer.trace("wsgi.response") as resp_span:
                if hasattr(result, "__class__"):
                    resp_span.meta["result_class"] = getattr(getattr(result, "__class__", None), "__name__")

                for chunk in result:
                    yield chunk

            if hasattr(result, "close"):
                try:
                    result.close()
                except Exception:
                    typ, val, tb = sys.exc_info()
                    span.set_exc_info(typ, val, tb)
                    six.reraise(typ, val, tb=tb)
