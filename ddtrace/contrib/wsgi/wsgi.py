import sys

from ddtrace.internal.compat import PY2


if PY2:
    import exceptions

    generatorExit = exceptions.GeneratorExit
else:
    import builtins

    generatorExit = builtins.GeneratorExit


import six
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


class DDWSGIMiddleware(object):
    """WSGI middleware providing tracing around an application.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    :param span_modifier: Span modifier that can add tags to the root span.
                            Defaults to using the request method and url in the resource.
    """

    def __init__(self, application, tracer=None, span_modifier=default_wsgi_span_modifier):
        self.app = application
        self.tracer = tracer or ddtrace.tracer
        self.span_modifier = span_modifier

    def __call__(self, environ, start_response):
        def intercept_start_response(status, response_headers, exc_info=None):
            span = self.tracer.current_root_span()
            if span is not None:
                status_code, status_msg = status.split(" ", 1)
                span.set_tag("http.status_msg", status_msg)
                trace_utils.set_http_meta(span, config.wsgi, status_code=status_code, response_headers=response_headers)
            with self.tracer.trace(
                "wsgi.start_response",
                service=trace_utils.int_service(None, config.wsgi),
                span_type=SpanTypes.WEB,
            ):
                write = start_response(status, response_headers, exc_info)
            return write

        trace_utils.activate_distributed_headers(self.tracer, int_config=config.wsgi, request_headers=environ)

        with self.tracer.trace(
            "wsgi.request",
            service=trace_utils.int_service(None, config.wsgi),
            span_type=SpanTypes.WEB,
        ) as span:
            # This prevents GeneratorExit exceptions from being propagated to the top-level span.
            # This can occur if a streaming response exits abruptly leading to a broken pipe.
            # Note: The wsgi.response span will still have the error information included.
            span._ignore_exception(generatorExit)
            with self.tracer.trace("wsgi.application"):
                result = self.app(environ, intercept_start_response)

            with self.tracer.trace("wsgi.response") as resp_span:
                if hasattr(result, "__class__"):
                    resp_class = getattr(getattr(result, "__class__"), "__name__", None)
                    if resp_class:
                        resp_span.meta["result_class"] = resp_class

                for chunk in result:
                    yield chunk

            url = construct_url(environ)
            method = environ.get("REQUEST_METHOD")
            query_string = environ.get("QUERY_STRING")
            request_headers = get_request_headers(environ)
            trace_utils.set_http_meta(
                span, config.wsgi, method=method, url=url, query=query_string, request_headers=request_headers
            )

            if self.span_modifier:
                self.span_modifier(span, environ)

            if hasattr(result, "close"):
                try:
                    result.close()
                except Exception:
                    typ, val, tb = sys.exc_info()
                    span.set_exc_info(typ, val, tb)
                    six.reraise(typ, val, tb=tb)
