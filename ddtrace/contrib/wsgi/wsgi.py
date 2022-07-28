import functools
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Iterable
    from typing import Optional

    from ddtrace import Pin
    from ddtrace import Span
    from ddtrace import Tracer
    from ddtrace.settings import Config


from six.moves.urllib.parse import quote

import ddtrace
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.propagation._utils import from_wsgi_header
from ddtrace.propagation.http import HTTPPropagator

from .. import trace_utils


log = get_logger(__name__)

propagator = HTTPPropagator

config._add(
    "wsgi",
    dict(
        _default_service="wsgi",
        distributed_tracing=True,
    ),
)


class _DDWSGIMiddlewareBase(object):
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
        self._config = int_config
        self._pin = pin

    @property
    def _request_span_name(self):
        # type: () -> str
        "Returns the name of a request span. Example: `flask.request`"
        raise NotImplementedError

    @property
    def _application_span_name(self):
        # type: () -> str
        "Returns the name of an application span. Example: `flask.application`"
        raise NotImplementedError

    @property
    def _response_span_name(self):
        # type: () -> str
        "Returns the name of a response span. Example: `flask.response`"
        raise NotImplementedError

    def __call__(self, environ, start_response):
        # type: (Iterable, Callable) -> None
        trace_utils.activate_distributed_headers(self.tracer, int_config=self._config, request_headers=environ)

        with self.tracer.trace(
            self._request_span_name,
            service=trace_utils.int_service(self._pin, self._config),
            span_type=SpanTypes.WEB,
        ) as req_span:
            # This prevents GeneratorExit exceptions from being set on the request span.
            req_span._ignore_exception(GeneratorExit)
            self._request_span_modifier(req_span, environ)

            with self.tracer.trace(self._application_span_name) as app_span:
                intercept_start_response = functools.partial(self._traced_start_response, start_response, req_span)
                result = self.app(environ, intercept_start_response)
                self._application_span_modifier(app_span, environ, result)

            with self.tracer.trace(self._response_span_name) as resp_span:
                # This prevents GeneratorExit exceptions from being set on the response span.
                # This can occur if a streaming response exits abruptly leading to a broken pipe.
                resp_span._ignore_exception(GeneratorExit)
                self._response_span_modifier(resp_span, result)
                for chunk in result:
                    yield chunk

            if hasattr(result, "close"):
                result.close()

    def _traced_start_response(self, start_response, request_span, status, environ, exc_info=None):
        # type: (Callable, Span, str, Dict, Any) -> None
        """sets the status code on a request span when start_response is called"""
        status_code, _ = status.split(" ", 1)
        trace_utils.set_http_meta(request_span, self._config, status_code=status_code)
        return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span, environ):
        # type: (Span, Dict) -> None
        """Implement to modify span attributes on the request_span"""
        pass

    def _application_span_modifier(self, app_span, environ, result):
        # type: (Span, Dict, Iterable) -> None
        """Implement to modify span attributes on the application_span"""
        pass

    def _response_span_modifier(self, resp_span, response):
        # type: (Span, Dict) -> None
        """Implement to modify span attributes on the request_span"""
        pass


def construct_url(environ):
    """
    https://www.python.org/dev/peps/pep-3333/#url-reconstruction
    """
    url = environ["wsgi.url_scheme"] + "://"

    if environ.get("HTTP_HOST"):
        url += environ["HTTP_HOST"]
    else:
        url += environ["SERVER_NAME"]

        if environ["wsgi.url_scheme"] == "https":
            if environ["SERVER_PORT"] != "443":
                url += ":" + environ["SERVER_PORT"]
        else:
            if environ["SERVER_PORT"] != "80":
                url += ":" + environ["SERVER_PORT"]

    url += quote(environ.get("SCRIPT_NAME", ""))
    url += quote(environ.get("PATH_INFO", ""))
    if environ.get("QUERY_STRING"):
        url += "?" + environ["QUERY_STRING"]

    return url


def get_request_headers(environ):
    """
    Manually grab the request headers from the environ dictionary.
    """
    request_headers = {}
    for key in environ.keys():
        if key.startswith("HTTP"):
            request_headers[from_wsgi_header(key)] = environ[key]
    return request_headers


def default_wsgi_span_modifier(span, environ):
    span.resource = "{} {}".format(environ["REQUEST_METHOD"], environ["PATH_INFO"])


class DDWSGIMiddleware(_DDWSGIMiddlewareBase):
    """WSGI middleware providing tracing around an application.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    :param span_modifier: Span modifier that can add tags to the root span.
                            Defaults to using the request method and url in the resource.
    """

    _request_span_name = "wsgi.request"
    _application_span_name = "wsgi.application"
    _response_span_name = "wsgi.response"

    def __init__(self, application, tracer=None, span_modifier=default_wsgi_span_modifier):
        # type: (Iterable, Optional[Tracer], Callable[[Span, Dict[str, str]], None]) -> None
        super(DDWSGIMiddleware, self).__init__(application, tracer or ddtrace.tracer, config.wsgi, None)
        self.span_modifier = span_modifier

    def _traced_start_response(self, start_response, request_span, status, environ, exc_info=None):
        status_code, status_msg = status.split(" ", 1)
        request_span.set_tag("http.status_msg", status_msg)
        trace_utils.set_http_meta(request_span, self._config, status_code=status_code, response_headers=environ)

        with self.tracer.trace(
            "wsgi.start_response",
            service=trace_utils.int_service(None, self._config),
            span_type=SpanTypes.WEB,
        ):
            return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span, environ):
        url = construct_url(environ)
        method = environ.get("REQUEST_METHOD")
        query_string = environ.get("QUERY_STRING")
        request_headers = get_request_headers(environ)
        trace_utils.set_http_meta(
            req_span, self._config, method=method, url=url, query=query_string, request_headers=request_headers
        )
        if self.span_modifier:
            self.span_modifier(req_span, environ)

    def _response_span_modifier(self, resp_span, response):
        if hasattr(response, "__class__"):
            resp_class = getattr(getattr(response, "__class__"), "__name__", None)
            if resp_class:
                resp_span._set_str_tag("result_class", resp_class)
