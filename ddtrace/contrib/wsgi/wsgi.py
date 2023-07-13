import sys
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

import ddtrace
from ddtrace import config
from ddtrace import trace_handlers
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.propagation._utils import from_wsgi_header
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor import wrapt

from .. import trace_utils
from ...appsec._constants import WAF_CONTEXT_NAMES
from ...constants import SPAN_KIND
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


class _TracedIterable(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, parent_span):
        super(_TracedIterable, self).__init__(wrapped)
        self._self_span = span
        self._self_parent_span = parent_span
        self._self_span_finished = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.__wrapped__)
        except StopIteration:
            self._finish_spans()
            raise
        except Exception:
            self._self_span.set_exc_info(*sys.exc_info())
            self._finish_spans()
            raise

    # PY2 Support
    next = __next__

    def close(self):
        if getattr(self.__wrapped__, "close", None):
            self.__wrapped__.close()
        self._finish_spans()

    def _finish_spans(self):
        if not self._self_span_finished:
            self._self_span.finish()
            self._self_parent_span.finish()
            self._self_span_finished = True

    def __getattribute__(self, name):
        if name == "__len__":
            # __len__ is defined by the parent class, wrapt.ObjectProxy.
            # However this attribute should not be defined for iterables.
            # By definition, iterables should not support len(...).
            raise AttributeError("__len__ is not supported")
        return super(_TracedIterable, self).__getattribute__(name)


class _ContextBuilderWSGIMiddlewareBase(object):
    """Base WSGI middleware class.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    :param int_config: Integration specific configuration object.
    :param pin: Set tracing metadata on a particular traced connection
    """

    def __init__(self, application, int_config):
        # type: (Iterable, Config) -> None
        self.app = application
        self._config = int_config

    def __call__(self, environ, start_response):
        # type: (Iterable, Callable) -> _TracedIterable
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
            if core.get_item(WAF_CONTEXT_NAMES.BLOCKED):
                ctype, content = core.dispatch("wsgi.block.started", [ctx])[0][0]
                start_response("403 FORBIDDEN", [("content-type", ctype)])
                closing_iterator = [content]
                not_blocked = False

            def blocked_view():
                ctype, content = core.dispatch("wsgi.block.started", [ctx])[0][0]
                return content, 403, [("content-type", ctype)]

            core.dispatch("wsgi.block_decided", [blocked_view])

            if not_blocked:
                core.dispatch("wsgi.request.prepare", [ctx, start_response])
                try:
                    closing_iterator = self.app(environ, ctx.get_item("intercept_start_response"))
                except BaseException:
                    core.dispatch("wsgi.app.exception", [ctx])
                    raise
                else:
                    core.dispatch("wsgi.app.success", [ctx, closing_iterator])
                if core.get_item(WAF_CONTEXT_NAMES.BLOCKED):
                    _, content = core.dispatch("wsgi.block.started", [ctx])[0][0]
                    closing_iterator = [content]

            return core.dispatch("wsgi.request.complete", [ctx, closing_iterator])[0][0]


class _DDWSGIMiddlewareBase(_ContextBuilderWSGIMiddlewareBase):
    """Base WSGI middleware class.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    :param int_config: Integration specific configuration object.
    :param pin: Set tracing metadata on a particular traced connection
    """

    def __init__(self, application, tracer, int_config, pin):
        # type: (Iterable, Tracer, Config, Pin) -> None
        super(_DDWSGIMiddlewareBase, self).__init__(application, int_config)
        self.tracer = tracer
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

    def _traced_start_response(self, start_response, request_span, app_span, status, environ, exc_info=None):
        # type: (Callable, Span, Span, str, Dict, Any) -> None
        """sets the status code on a request span when start_response is called"""
        status_code, _ = status.split(" ", 1)
        trace_utils.set_http_meta(request_span, self._config, status_code=status_code)
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
        status_code, status_msg = status.split(" ", 1)
        request_span.set_tag_str(http.STATUS_MSG, status_msg)
        trace_utils.set_http_meta(request_span, self._config, status_code=status_code, response_headers=environ)

        with self.tracer.start_span(
            "wsgi.start_response",
            child_of=app_span,
            service=trace_utils.int_service(None, self._config),
            span_type=SpanTypes.WEB,
            activate=True,
        ) as span:
            span.set_tag_str(COMPONENT, self._config.integration_name)

            # set span.kind to the type of operation being performed
            span.set_tag_str(SPAN_KIND, SpanKind.SERVER)

            return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span, environ, parsed_headers=None):
        url = construct_url(environ)
        method = environ.get("REQUEST_METHOD")
        query_string = environ.get("QUERY_STRING")
        request_headers = parsed_headers if parsed_headers is not None else get_request_headers(environ)
        trace_utils.set_http_meta(
            req_span, self._config, method=method, url=url, query=query_string, request_headers=request_headers
        )
        if self.span_modifier:
            self.span_modifier(req_span, environ)

    def _response_span_modifier(self, resp_span, response):
        if hasattr(response, "__class__"):
            resp_class = getattr(getattr(response, "__class__"), "__name__", None)
            if resp_class:
                resp_span.set_tag_str("result_class", resp_class)
