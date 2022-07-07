import functools
import sys
from typing import TYPE_CHECKING

from ddtrace.internal.compat import PY2


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Iterable

    from ddtrace import Pin
    from ddtrace import Span
    from ddtrace import Tracer
    from ddtrace.settings import Config


if PY2:
    import exceptions

    generatorExit = exceptions.GeneratorExit
else:
    import builtins

    generatorExit = builtins.GeneratorExit


import six

from ddtrace.ext import SpanTypes

from .. import trace_utils


__metaclass__ = type


class DDWSGIMiddlewareBase:
    """Base WSGI middleware class.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    :param int_config: Integration specific configuration object.
    :param pin: Set tracing metadata on a particular traced connection
    """

    def __init__(self, application, tracer, int_config, pin):
        # type: (Iterable, Tracer, Config, Pin) -> None
        self.app = application
        self.tracer = tracer
        self.config = int_config
        self.pin = pin

    @property
    def request_span(self):
        # type: () -> str
        raise NotImplementedError

    @property
    def application_span(self):
        # type: () -> str
        raise NotImplementedError

    @property
    def response_span(self):
        # type: () -> str
        raise NotImplementedError

    def __call__(self, environ, start_response):
        # type: (Iterable, Callable) -> None
        trace_utils.activate_distributed_headers(self.tracer, int_config=self.config, request_headers=environ)

        with self.tracer.trace(
            self.request_span,
            service=trace_utils.int_service(self.pin, self.config),
            span_type=SpanTypes.WEB,
        ) as req_span:
            # This prevents GeneratorExit exceptions from being propagated to the top-level span.
            # This can occur if a streaming response exits abruptly leading to a broken pipe.
            # Note: The wsgi.response span will still have the error information included.
            req_span._ignore_exception(generatorExit)

            self.request_span_modifier(req_span, environ)

            with self.tracer.trace(self.application_span) as app_span:
                intercept_start_response = functools.partial(self.traced_start_response, start_response, req_span)
                result = self.app(environ, intercept_start_response)
                self.application_span_modifier(app_span, environ, result)

            with self.tracer.trace(self.response_span) as resp_span:
                self.response_span_modifier(resp_span, result)
                for chunk in result:
                    yield chunk

            if hasattr(result, "close"):
                try:
                    result.close()
                except Exception:
                    typ, val, tb = sys.exc_info()
                    req_span.set_exc_info(typ, val, tb)
                    six.reraise(typ, val, tb=tb)

    def traced_start_response(self, start_response, request_span, status, environ, exc_info=None):
        # type: (Callable, Span, str, Dict, Any) -> None
        """sets the status code on a request span when start_response is called"""
        status_code, _ = status.split(" ", 1)
        trace_utils.set_http_meta(request_span, self.config, status_code=status_code)
        return start_response(status, environ, exc_info)

    def request_span_modifier(self, req_span, environ):
        # type: (Span, Dict) -> None
        """Implement to modify span attributes on the request_span"""
        pass

    def application_span_modifier(self, app_span, environ, result):
        # type: (Span, Dict, Iterable) -> None
        """Implement to modify span attributes on the application_span"""
        pass

    def response_span_modifier(self, resp_span, response):
        # type: (Span, Dict) -> None
        """Implement to modify span attributes on the request_span"""
        pass
