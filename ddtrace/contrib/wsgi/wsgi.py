from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Callable
    from typing import Iterable

    from ddtrace import Tracer


from six.moves.urllib.parse import quote

import ddtrace
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.propagation._utils import from_wsgi_header
from ddtrace.propagation.http import HTTPPropagator

from .. import trace_utils
from ._utils import DDWSGIMiddlewareBase


log = get_logger(__name__)

propagator = HTTPPropagator

config._add(
    "wsgi",
    dict(
        _default_service="wsgi",
        distributed_tracing=True,
    ),
)


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


class DDWSGIMiddleware(DDWSGIMiddlewareBase):
    """WSGI middleware providing tracing around an application.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    :param span_modifier: Span modifier that can add tags to the root span.
                            Defaults to using the request method and url in the resource.
    """

    request_span = "wsgi.request"
    application_span = "wsgi.application"
    response_span = "wsgi.response"

    def __init__(self, application, tracer=None, span_modifier=default_wsgi_span_modifier):
        # type: (Iterable, Tracer, Callable) -> None
        super(DDWSGIMiddleware, self).__init__(application, tracer or ddtrace.tracer, config.wsgi, None)
        self.span_modifier = span_modifier

    def traced_start_response(self, start_response, request_span, status, environ, exc_info=None):
        status_code, status_msg = status.split(" ", 1)
        request_span.set_tag("http.status_msg", status_msg)
        trace_utils.set_http_meta(request_span, self.config, status_code=status_code, response_headers=environ)

        with self.tracer.trace(
            "wsgi.start_response",
            service=trace_utils.int_service(None, self.config),
            span_type=SpanTypes.WEB,
        ):
            return start_response(status, environ, exc_info)

    def request_span_modifier(self, req_span, environ):
        url = construct_url(environ)
        method = environ.get("REQUEST_METHOD")
        query_string = environ.get("QUERY_STRING")
        request_headers = get_request_headers(environ)
        trace_utils.set_http_meta(
            req_span, self.config, method=method, url=url, query=query_string, request_headers=request_headers
        )
        if self.span_modifier:
            self.span_modifier(req_span, environ)

    def response_span_modifier(self, resp_span, response):
        if hasattr(response, "__class__"):
            resp_class = getattr(getattr(response, "__class__"), "__name__", None)
            if resp_class:
                resp_span._set_str_tag("result_class", resp_class)
