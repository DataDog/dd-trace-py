from typing import TYPE_CHECKING
from typing import Callable
from typing import Iterable

from ddtrace.internal.schema.span_attribute_schema import SpanDirection


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any  # noqa:F401
    from typing import Dict  # noqa:F401
    from typing import Mapping  # noqa:F401
    from typing import Optional  # noqa:F401

    from ddtrace import Pin  # noqa:F401
    from ddtrace import Span  # noqa:F401
    from ddtrace import Tracer  # noqa:F401
    from ddtrace.settings import Config  # noqa:F401

from six.moves.urllib.parse import quote

import ddtrace
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import HTTP_REQUEST_BLOCKED
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.propagation._utils import from_wsgi_header
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor import wrapt

from ...internal import core


log = get_logger(__name__)

propagator = HTTPPropagator

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
    :param app_is_iterator: Boolean indicating whether the wrapped app is a Python iterator
    """

    def __init__(self, application, tracer, int_config, pin, app_is_iterator=False):
        # type: (Iterable, Tracer, Config, Pin, bool) -> None
        self.app = application
        self.tracer = tracer
        self._config = int_config
        self._pin = pin
        self.app_is_iterator = app_is_iterator

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

    def __call__(self, environ: Iterable, start_response: Callable) -> wrapt.ObjectProxy:
        headers = get_request_headers(environ)
        closing_iterable = ()
        not_blocked = True
        with core.context_with_data(
            "wsgi.__call__",
            remote_addr=environ.get("REMOTE_ADDR"),
            headers=headers,
            headers_case_sensitive=True,
            service=trace_utils.int_service(self._pin, self._config),
            span_type=SpanTypes.WEB,
            span_name=(self._request_call_name if hasattr(self, "_request_call_name") else self._request_span_name),
            middleware_config=self._config,
            distributed_headers_config=self._config,
            distributed_headers=environ,
            environ=environ,
            middleware=self,
            call_key="req_span",
        ) as ctx:
            if core.get_item(HTTP_REQUEST_BLOCKED):
                result = core.dispatch_with_results("wsgi.block.started", (ctx, construct_url)).status_headers_content
                if result:
                    status, headers, content = result.value
                else:
                    status, headers, content = 403, [], ""
                start_response(str(status), headers)
                closing_iterable = [content]
                not_blocked = False

            def blocked_view():
                result = core.dispatch_with_results("wsgi.block.started", (ctx, construct_url)).status_headers_content
                if result:
                    status, headers, content = result.value
                else:
                    status, headers, content = 403, [], ""
                return content, status, headers

            core.dispatch("wsgi.block_decided", (blocked_view,))

            if not_blocked:
                core.dispatch("wsgi.request.prepare", (ctx, start_response))
                try:
                    closing_iterable = self.app(environ, ctx.get_item("intercept_start_response"))
                except BaseException:
                    core.dispatch("wsgi.app.exception", (ctx,))
                    raise
                else:
                    core.dispatch("wsgi.app.success", (ctx, closing_iterable))
                if core.get_item(HTTP_REQUEST_BLOCKED):
                    _, _, content = core.dispatch_with_results(
                        "wsgi.block.started", (ctx, construct_url)
                    ).status_headers_content.value or (None, None, "")
                    closing_iterable = [content]

            result = core.dispatch_with_results(
                "wsgi.request.complete", (ctx, closing_iterable, self.app_is_iterator)
            ).traced_iterable
            return result.value if result else []

    def _traced_start_response(self, start_response, request_span, app_span, status, environ, exc_info=None):
        # type: (Callable, Span, Span, str, Dict, Any) -> None
        """sets the status code on a request span when start_response is called"""
        with core.context_with_data(
            "wsgi.response",
            middleware=self,
            request_span=request_span,
            parent_call=app_span,
            status=status,
            environ=environ,
            span_type=SpanTypes.WEB,
            service=trace_utils.int_service(None, self._config),
            start_span=False,
            tags={COMPONENT: self._config.integration_name, SPAN_KIND: SpanKind.SERVER},
            call_key="response_span",
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
    :param app_is_iterator: Boolean indicating whether the wrapped WSGI app is a Python iterator
    """

    _request_span_name = schematize_url_operation("wsgi.request", protocol="http", direction=SpanDirection.INBOUND)
    _application_span_name = "wsgi.application"
    _response_span_name = "wsgi.response"

    def __init__(self, application, tracer=None, span_modifier=default_wsgi_span_modifier, app_is_iterator=False):
        # type: (Iterable, Optional[Tracer], Callable[[Span, Dict[str, str]], None], bool) -> None
        super(DDWSGIMiddleware, self).__init__(
            application, tracer or ddtrace.tracer, config.wsgi, None, app_is_iterator=app_is_iterator
        )
        self.span_modifier = span_modifier

    def _traced_start_response(self, start_response, request_span, app_span, status, environ, exc_info=None):
        with core.context_with_data(
            "wsgi.response",
            middleware=self,
            request_span=request_span,
            parent_call=app_span,
            status=status,
            environ=environ,
            span_type=SpanTypes.WEB,
            span_name="wsgi.start_response",
            service=trace_utils.int_service(None, self._config),
            start_span=True,
            tags={COMPONENT: self._config.integration_name, SPAN_KIND: SpanKind.SERVER},
            call_key="response_span",
        ) as ctx, ctx.get_item("response_span"):
            return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span, environ, parsed_headers=None):
        url = construct_url(environ)
        request_headers = parsed_headers if parsed_headers is not None else get_request_headers(environ)
        core.dispatch("wsgi.request.prepared", (self, req_span, url, request_headers, environ))

    def _response_span_modifier(self, resp_span, response):
        core.dispatch("wsgi.response.prepared", (resp_span, response))
