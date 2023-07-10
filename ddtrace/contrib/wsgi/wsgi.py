import functools
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec import _asm_request_context
from ddtrace.internal.schema.span_attribute_schema import SpanDirection

from ...appsec._constants import SPAN_DATA_NAMES
from ..trace_utils import _get_request_header_user_agent
from ..trace_utils import _set_url_tag


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
from ...appsec import utils
from ...appsec._constants import WAF_CONTEXT_NAMES
from ...constants import SPAN_KIND
from ...internal import _context


log = get_logger(__name__)

propagator = HTTPPropagator

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

    def _make_block_content(self, environ, headers, span):
        ctype = "text/html" if "text/html" in headers.get("Accept", "").lower() else "text/json"
        content = utils._get_blocked_template(ctype).encode("UTF-8")
        try:
            span.set_tag_str(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-length", str(len(content)))
            span.set_tag_str(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type", ctype)
            span.set_tag_str(http.STATUS_CODE, "403")
            url = construct_url(environ)
            query_string = environ.get("QUERY_STRING")
            _set_url_tag(self._config, span, url, query_string)
            if query_string and self._config.trace_query_string:
                span.set_tag_str(http.QUERY_STRING, query_string)
            method = environ.get("REQUEST_METHOD")
            if method:
                span.set_tag_str(http.METHOD, method)
            user_agent = _get_request_header_user_agent(headers, headers_are_case_sensitive=True)
            if user_agent:
                span.set_tag_str(http.USER_AGENT, user_agent)
        except Exception as e:
            log.warning("Could not set some span tags on blocked request: %s", str(e))  # noqa: G200

        return ctype, content

    def __call__(self, environ, start_response):
        # type: (Iterable, Callable) -> _TracedIterable
        trace_utils.activate_distributed_headers(self.tracer, int_config=self._config, request_headers=environ)

        headers = get_request_headers(environ)
        closing_iterator = ()
        not_blocked = True
        with _asm_request_context.asm_request_context_manager(
            environ.get("REMOTE_ADDR"), headers, headers_case_sensitive=True
        ):
            req_span = self.tracer.trace(
                self._request_span_name,
                service=trace_utils.int_service(self._pin, self._config),
                span_type=SpanTypes.WEB,
            )

            if self.tracer._appsec_enabled:
                # [IP Blocking]
                if _context.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=req_span):
                    ctype, content = self._make_block_content(environ, headers, req_span)
                    start_response("403 FORBIDDEN", [("content-type", ctype)])
                    closing_iterator = [content]
                    not_blocked = False

                # [Suspicious Request Blocking on request]
                def blocked_view():
                    ctype, content = self._make_block_content(environ, headers, req_span)
                    return content, 403, [("content-type", ctype)]

                _asm_request_context.set_value(_asm_request_context._CALLBACKS, "flask_block", blocked_view)

            if not_blocked:
                req_span.set_tag_str(COMPONENT, self._config.integration_name)
                # set span.kind to the type of operation being performed
                req_span.set_tag_str(SPAN_KIND, SpanKind.SERVER)
                self._request_span_modifier(req_span, environ)
                try:
                    app_span = self.tracer.trace(self._application_span_name)

                    app_span.set_tag_str(COMPONENT, self._config.integration_name)

                    intercept_start_response = functools.partial(
                        self._traced_start_response, start_response, req_span, app_span
                    )
                    closing_iterator = self.app(environ, intercept_start_response)
                    self._application_span_modifier(app_span, environ, closing_iterator)
                    app_span.finish()
                except BaseException:
                    req_span.set_exc_info(*sys.exc_info())
                    app_span.set_exc_info(*sys.exc_info())
                    app_span.finish()
                    req_span.finish()
                    raise
                if self.tracer._appsec_enabled and _context.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=req_span):
                    # [Suspicious Request Blocking on request or response]
                    _, content = self._make_block_content(environ, headers, req_span)
                    closing_iterator = [content]

            # start flask.response span. This span will be finished after iter(result) is closed.
            # start_span(child_of=...) is used to ensure correct parenting.
            resp_span = self.tracer.start_span(self._response_span_name, child_of=req_span, activate=True)

            resp_span.set_tag_str(COMPONENT, self._config.integration_name)

            self._response_span_modifier(resp_span, closing_iterator)

            return _TracedIterable(iter(closing_iterator), resp_span, req_span)

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
