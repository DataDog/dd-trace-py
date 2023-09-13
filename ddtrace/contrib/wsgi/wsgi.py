from typing import TYPE_CHECKING

from ddtrace.internal.schema.span_attribute_schema import SpanDirection


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Iterable
    from typing import Mapping
    from typing import Optional

    from ddtrace import Pin
    from ddtrace import Span
    from ddtrace import Tracer
    from ddtrace.settings import Config

from six.moves.urllib.parse import quote
import wrapt

import ddtrace
from ddtrace import config
from ddtrace.internal.constants import HTTP_REQUEST_BLOCKED
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.propagation._utils import from_wsgi_header
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.tracing import trace_handlers

from ...internal import core


log = get_logger(__name__)

propagator = HTTPPropagator

trace_handlers.listen()

config._add(
    "wsgi",
    dict(
        _default_service="wsgi",
        distributed_tracing=True,
    ),
)


def get_version():
    # type: () -> str
    return ""


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
        # type: (Iterable, Callable) -> wrapt.ObjectProxy
        headers = get_request_headers(environ)
        closing_iterator = ()
        not_blocked = True
        with core.context_with_data(
            "wsgi.__call__",
            remote_addr=environ.get("REMOTE_ADDR"),
            headers=headers,
            headers_case_sensitive=True,
            environ=environ,
            middleware=self,
        ) as ctx:
            if core.get_item(HTTP_REQUEST_BLOCKED):
                status, headers, content = core.dispatch("wsgi.block.started", ctx, construct_url)[0][0]
                start_response(str(status), headers)
                closing_iterator = [content]
                not_blocked = False

            def blocked_view():
                status, headers, content = core.dispatch("wsgi.block.started", ctx, construct_url)[0][0]
                return content, status, headers

            core.dispatch("wsgi.block_decided", blocked_view)

            if not_blocked:
                core.dispatch("wsgi.request.prepare", ctx, start_response)
                try:
                    closing_iterator = self.app(environ, ctx.get_item("intercept_start_response"))
                except BaseException:
                    core.dispatch("wsgi.app.exception", ctx)
                    raise
                else:
                    core.dispatch("wsgi.app.success", ctx, closing_iterator)
                if core.get_item(HTTP_REQUEST_BLOCKED):
                    _, _, content = core.dispatch("wsgi.block.started", ctx, construct_url)[0][0]
                    closing_iterator = [content]

            return core.dispatch("wsgi.request.complete", ctx, closing_iterator)[0][0]

    def _traced_start_response(self, start_response, request_span, app_span, status, environ, exc_info=None):
        # type: (Callable, Span, Span, str, Dict, Any) -> None
        """sets the status code on a request span when start_response is called"""
        with core.context_with_data(
            "wsgi.response",
            middleware=self,
            request_span=request_span,
            app_span=app_span,
            status=status,
            environ=environ,
            start_span=False,
        ):
            return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span, environ, parsed_headers=None):
        # type: (Span, Dict, Optional[Dict]) -> None
        """Implement to modify span attributes on the request_span"""

    def _application_span_modifier(self, app_span, environ, result):
        # type: (Span, Dict, Iterable) -> None
        """Implement to modify span attributes on the application_span"""

    def _response_span_modifier(self, resp_span, response):
        # type: (Span, Dict) -> None
        """Implement to modify span attributes on the request_span"""


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
    # type: (Mapping[str, str]) -> Mapping[str, str]
    """
    Manually grab the request headers from the environ dictionary.
    """
    request_headers = {}  # type: Mapping[str, str]
    for key in environ.keys():
        if key.startswith("HTTP_"):
            name = from_wsgi_header(key)
            if name:
                request_headers[name] = environ[key]
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

    _request_span_name = schematize_url_operation("wsgi.request", protocol="http", direction=SpanDirection.INBOUND)
    _application_span_name = "wsgi.application"
    _response_span_name = "wsgi.response"

    def __init__(self, application, tracer=None, span_modifier=default_wsgi_span_modifier):
        # type: (Iterable, Optional[Tracer], Callable[[Span, Dict[str, str]], None]) -> None
        super(DDWSGIMiddleware, self).__init__(application, tracer or ddtrace.tracer, config.wsgi, None)
        self.span_modifier = span_modifier

    def _traced_start_response(self, start_response, request_span, app_span, status, environ, exc_info=None):
        with core.context_with_data(
            "wsgi.response",
            middleware=self,
            request_span=request_span,
            app_span=app_span,
            status=status,
            environ=environ,
            start_span=True,
        ) as ctx, ctx.get_item("response_span"):
            return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span, environ, parsed_headers=None):
        url = construct_url(environ)
        request_headers = parsed_headers if parsed_headers is not None else get_request_headers(environ)
        core.dispatch("wsgi.request.prepared", self, req_span, url, request_headers, environ)

    def _response_span_modifier(self, resp_span, response):
        core.dispatch("wsgi.response.prepared", resp_span, response)
