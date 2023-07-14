import functools
import sys

from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.trace_utils import _get_request_header_user_agent
from ddtrace.contrib.trace_utils import _set_url_tag
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import RESPONSE_HEADERS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import http as http_utils
from ddtrace.vendor import wrapt


log = get_logger(__name__)


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


def _on_context_started(ctx):
    middleware = ctx.get_item("middleware")
    environ = ctx.get_item("environ")
    trace_utils.activate_distributed_headers(middleware.tracer, int_config=middleware._config, request_headers=environ)
    req_span = middleware.tracer.trace(
        middleware._request_span_name,
        service=trace_utils.int_service(middleware._pin, middleware._config),
        span_type=SpanTypes.WEB,
    )
    ctx.set_item("req_span", req_span)


def _make_block_content(ctx, construct_url):
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    headers = ctx.get_item("headers")
    environ = ctx.get_item("environ")
    assert req_span is not None
    ctype = "text/html" if "text/html" in headers.get("Accept", "").lower() else "text/json"
    content = http_utils._get_blocked_template(ctype).encode("UTF-8")
    try:
        req_span.set_tag_str(RESPONSE_HEADERS + ".content-length", str(len(content)))
        req_span.set_tag_str(RESPONSE_HEADERS + ".content-type", ctype)
        req_span.set_tag_str(http.STATUS_CODE, "403")
        url = construct_url(environ)
        query_string = environ.get("QUERY_STRING")
        _set_url_tag(middleware._config, req_span, url, query_string)
        if query_string and middleware._config.trace_query_string:
            req_span.set_tag_str(http.QUERY_STRING, query_string)
        method = environ.get("REQUEST_METHOD")
        if method:
            req_span.set_tag_str(http.METHOD, method)
        user_agent = _get_request_header_user_agent(headers, headers_are_case_sensitive=True)
        if user_agent:
            req_span.set_tag_str(http.USER_AGENT, user_agent)
    except Exception as e:
        log.warning("Could not set some span tags on blocked request: %s", str(e))  # noqa: G200

    return ctype, content


def _on_request_prepare(ctx, start_response):
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    req_span.set_tag_str(COMPONENT, middleware._config.integration_name)
    # set span.kind to the type of operation being performed
    req_span.set_tag_str(SPAN_KIND, SpanKind.SERVER)
    middleware._request_span_modifier(req_span, ctx.get_item("environ"))
    app_span = middleware.tracer.trace(middleware._application_span_name)

    app_span.set_tag_str(COMPONENT, middleware._config.integration_name)
    ctx.set_item("app_span", app_span)

    intercept_start_response = functools.partial(middleware._traced_start_response, start_response, req_span, app_span)
    ctx.set_item("intercept_start_response", intercept_start_response)


def _on_app_success(ctx, closing_iterator):
    app_span = ctx.get_item("app_span")
    ctx.get_item("middleware")._application_span_modifier(app_span, ctx.get_item("environ"), closing_iterator)
    app_span.finish()


def _on_app_exception(ctx):
    req_span = ctx.get_item("req_span")
    app_span = ctx.get_item("app_span")
    req_span.set_exc_info(*sys.exc_info())
    app_span.set_exc_info(*sys.exc_info())
    app_span.finish()
    req_span.finish()


def _on_request_complete(ctx, closing_iterator):
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    # start flask.response span. This span will be finished after iter(result) is closed.
    # start_span(child_of=...) is used to ensure correct parenting.
    resp_span = middleware.tracer.start_span(middleware._response_span_name, child_of=req_span, activate=True)

    resp_span.set_tag_str(COMPONENT, middleware._config.integration_name)

    middleware._response_span_modifier(resp_span, closing_iterator)

    return _TracedIterable(iter(closing_iterator), resp_span, req_span)


def _on_response_started(middleware, request_span, app_span, status, environ):
    status_code, status_msg = status.split(" ", 1)
    request_span.set_tag_str(http.STATUS_MSG, status_msg)
    trace_utils.set_http_meta(request_span, middleware._config, status_code=status_code, response_headers=environ)

    span = middleware.tracer.start_span(
        "wsgi.start_response",
        child_of=app_span,
        service=trace_utils.int_service(None, middleware._config),
        span_type=SpanTypes.WEB,
        activate=True,
    )
    span.set_tag_str(COMPONENT, middleware._config.integration_name)
    # set span.kind to the type of operation being performed
    span.set_tag_str(SPAN_KIND, SpanKind.SERVER)
    return span


def listen():
    core.on("context.started.wsgi.__call__", _on_context_started)
    core.on("wsgi.block.started", _make_block_content)
    core.on("wsgi.request.prepare", _on_request_prepare)
    core.on("wsgi.app.success", _on_app_success)
    core.on("wsgi.app.exception", _on_app_exception)
    core.on("wsgi.request.complete", _on_request_complete)
    core.on("wsgi.response.start", _on_response_started)
