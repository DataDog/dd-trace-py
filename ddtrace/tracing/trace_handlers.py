import functools
import sys

import wrapt

from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.contrib.trace_utils import _get_request_header_user_agent
from ddtrace.contrib.trace_utils import _set_url_tag
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.compat import maybe_stringify
from ddtrace.internal.compat import nullcontext
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import FLASK_ENDPOINT
from ddtrace.internal.constants import FLASK_URL_RULE
from ddtrace.internal.constants import FLASK_VIEW_ARGS
from ddtrace.internal.constants import HTTP_REQUEST_BLOCKED
from ddtrace.internal.constants import RESPONSE_HEADERS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import http as http_utils


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
        middleware._request_call_name if hasattr(middleware, "_request_call_name") else middleware._request_span_name,
        service=trace_utils.int_service(middleware._pin, middleware._config),
        span_type=SpanTypes.WEB,
    )
    ctx.set_item("req_span", req_span)


def _make_block_content(ctx, construct_url):
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    headers = ctx.get_item("headers")
    environ = ctx.get_item("environ")
    if req_span is None:
        raise ValueError("request span not found")
    block_config = core.get_item(HTTP_REQUEST_BLOCKED, span=req_span)
    desired_type = block_config.get("type", "auto")
    ctype = None
    if desired_type == "none":
        content = ""
        resp_headers = [("content-type", "text/plain; charset=utf-8"), ("location", block_config.get("location", ""))]
    else:
        if desired_type == "auto":
            ctype = "text/html" if "text/html" in headers.get("Accept", "").lower() else "text/json"
        else:
            ctype = "text/" + block_config["type"]
        content = http_utils._get_blocked_template(ctype).encode("UTF-8")
        resp_headers = [("content-type", ctype)]
    status = block_config.get("status_code", 403)
    try:
        req_span.set_tag_str(RESPONSE_HEADERS + ".content-length", str(len(content)))
        if ctype is not None:
            req_span.set_tag_str(RESPONSE_HEADERS + ".content-type", ctype)
        req_span.set_tag_str(http.STATUS_CODE, str(status))
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

    return status, resp_headers, content


def _on_request_prepare(ctx, start_response):
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    req_span.set_tag_str(COMPONENT, middleware._config.integration_name)
    # set span.kind to the type of operation being performed
    req_span.set_tag_str(SPAN_KIND, SpanKind.SERVER)
    if hasattr(middleware, "_request_call_modifier"):
        modifier = middleware._request_call_modifier
        args = [ctx]
    else:
        modifier = middleware._request_span_modifier
        args = [req_span, ctx.get_item("environ")]
    modifier(*args)
    app_span = middleware.tracer.trace(
        middleware._application_call_name
        if hasattr(middleware, "_application_call_name")
        else middleware._application_span_name
    )

    app_span.set_tag_str(COMPONENT, middleware._config.integration_name)
    ctx.set_item("app_span", app_span)

    if hasattr(middleware, "_wrapped_start_response"):
        wrapped = middleware._wrapped_start_response
        args = [start_response, ctx]
    else:
        wrapped = middleware._traced_start_response
        args = [start_response, req_span, app_span]
    intercept_start_response = functools.partial(wrapped, *args)
    ctx.set_item("intercept_start_response", intercept_start_response)


def _on_app_success(ctx, closing_iterator):
    app_span = ctx.get_item("app_span")
    middleware = ctx.get_item("middleware")
    modifier = (
        middleware._application_call_modifier
        if hasattr(middleware, "_application_call_modifier")
        else middleware._application_span_modifier
    )
    modifier(app_span, ctx.get_item("environ"), closing_iterator)
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
    resp_span = middleware.tracer.start_span(
        middleware._response_call_name
        if hasattr(middleware, "_response_call_name")
        else middleware._response_span_name,
        child_of=req_span,
        activate=True,
    )

    resp_span.set_tag_str(COMPONENT, middleware._config.integration_name)

    modifier = (
        middleware._response_call_modifier
        if hasattr(middleware, "_response_call_modifier")
        else middleware._response_span_modifier
    )
    modifier(resp_span, closing_iterator)

    return _TracedIterable(iter(closing_iterator), resp_span, req_span)


def _on_response_context_started(ctx):
    status = ctx.get_item("status")
    request_span = ctx.get_item("request_span")
    middleware = ctx.get_item("middleware")
    environ = ctx.get_item("environ")
    start_span = ctx.get_item("start_span")
    app_span = ctx.get_item("app_span")
    status_code, status_msg = status.split(" ", 1)
    trace_utils.set_http_meta(request_span, middleware._config, status_code=status_code, response_headers=environ)
    if start_span:
        request_span.set_tag_str(http.STATUS_MSG, status_msg)

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
        ctx.set_item("response_span", span)


def _on_response_prepared(resp_span, response):
    if hasattr(response, "__class__"):
        resp_class = getattr(response.__class__, "__name__", None)
        if resp_class:
            resp_span.set_tag_str("result_class", resp_class)


def _on_request_prepared(middleware, req_span, url, request_headers, environ):
    method = environ.get("REQUEST_METHOD")
    query_string = environ.get("QUERY_STRING")
    trace_utils.set_http_meta(
        req_span, middleware._config, method=method, url=url, query=query_string, request_headers=request_headers
    )
    if middleware.span_modifier:
        middleware.span_modifier(req_span, environ)


def _set_request_tags(request, span, flask_config):
    try:
        span.set_tag_str(COMPONENT, flask_config.integration_name)

        if span.name.split(".")[-1] == "request":
            span.set_tag_str(SPAN_KIND, SpanKind.SERVER)

        # DEV: This name will include the blueprint name as well (e.g. `bp.index`)
        if not span.get_tag(FLASK_ENDPOINT) and request.endpoint:
            span.resource = " ".join((request.method, request.endpoint))
            span.set_tag_str(FLASK_ENDPOINT, request.endpoint)

        if not span.get_tag(FLASK_URL_RULE) and request.url_rule and request.url_rule.rule:
            span.resource = " ".join((request.method, request.url_rule.rule))
            span.set_tag_str(FLASK_URL_RULE, request.url_rule.rule)

        if not span.get_tag(FLASK_VIEW_ARGS) and request.view_args and flask_config.get("collect_view_args"):
            for k, v in request.view_args.items():
                # DEV: Do not use `set_tag_str` here since view args can be string/int/float/path/uuid/etc
                #      https://flask.palletsprojects.com/en/1.1.x/api/#url-route-registrations
                span.set_tag(".".join((FLASK_VIEW_ARGS, k)), v)
            trace_utils.set_http_meta(span, flask_config, request_path_params=request.view_args)
    except Exception:
        log.debug('failed to set tags for "flask.request" span', exc_info=True)


def _on_start_response_pre(request, ctx, flask_config, status_code, headers):
    span = ctx.get_item("req_span")
    code, _, _ = status_code.partition(" ")
    # If values are accessible, set the resource as `<method> <path>` and add other request tags
    _set_request_tags(request, span, flask_config)
    # Override root span resource name to be `<method> 404` for 404 requests
    # DEV: We do this because we want to make it easier to see all unknown requests together
    #      Also, we do this to reduce the cardinality on unknown urls
    # DEV: If we have an endpoint or url rule tag, then we don't need to do this,
    #      we still want `GET /product/<int:product_id>` grouped together,
    #      even if it is a 404
    if not span.get_tag(FLASK_ENDPOINT) and not span.get_tag(FLASK_URL_RULE):
        span.resource = " ".join((request.method, code))

    response_cookies = _cookies_from_response_headers(headers)
    trace_utils.set_http_meta(
        span,
        flask_config,
        status_code=code,
        response_headers=headers,
        route=span.get_tag(FLASK_URL_RULE),
        response_cookies=response_cookies,
    )


def _cookies_from_response_headers(response_headers):
    cookies = {}
    for header_tuple in response_headers:
        if header_tuple[0] == "Set-Cookie":
            cookie_tokens = header_tuple[1].split("=", 1)
            cookies[cookie_tokens[0]] = cookie_tokens[1]

    return cookies


def _on_traced_request_context_started_flask(ctx):
    """
    trace a Flask function while trying to extract endpoint information (endpoint, url_rule, view_args, etc)

    This wrapper will add identifier tags to the current span from `flask.app.Flask.wsgi_app`.
    """
    pin = ctx.get_item("pin")
    current_span = pin.tracer.current_span()
    if not pin.enabled or not current_span:
        ctx.set_item("flask_request_call", nullcontext())
        return

    ctx.set_item("current_span", current_span)
    flask_config = ctx.get_item("flask_config")
    service = trace_utils.int_service(pin, flask_config, pin)
    _set_request_tags(ctx.get_item("flask_request"), current_span, flask_config)
    request_span = pin.tracer.trace(ctx.get_item("name"), service=service)
    ctx.set_item("flask_request_call", request_span)
    request_span.set_tag_str(COMPONENT, flask_config.integration_name)
    request_span._ignore_exception(ctx.get_item("ignored_exception_type"))


def _on_jsonify_context_started_flask(ctx):
    span = ctx.get_item("pin").tracer.trace(ctx.get_item("name"))
    span.set_tag_str(COMPONENT, ctx.get_item("flask_config").integration_name)
    ctx.set_item("flask_jsonify_call", span)


def _on_flask_blocked_request(span):
    span.set_tag_str(http.STATUS_CODE, "403")
    request = core.get_item("flask_request")
    try:
        base_url = getattr(request, "base_url", None)
        query_string = getattr(request, "query_string", None)
        if base_url and query_string:
            _set_url_tag(core.get_item("flask_config"), span, base_url, query_string)
        if query_string and core.get_item("flask_config").trace_query_string:
            span.set_tag_str(http.QUERY_STRING, query_string)
        if request.method is not None:
            span.set_tag_str(http.METHOD, request.method)
        user_agent = _get_request_header_user_agent(request.headers)
        if user_agent:
            span.set_tag_str(http.USER_AGENT, user_agent)
    except Exception as e:
        log.warning("Could not set some span tags on blocked request: %s", str(e))  # noqa: G200


def _on_flask_render(template, flask_config):
    span = core.get_item("current_span")
    if not span:
        return
    name = maybe_stringify(getattr(template, "name", None) or flask_config.get("template_default_name"))
    if name is not None:
        span.resource = name
        span.set_tag_str("flask.template_name", name)


def _on_render_template_context_started_flask(ctx):
    name = ctx.get_item("name")
    span = ctx.get_item("pin").tracer.trace(name, span_type=SpanTypes.TEMPLATE)
    span.set_tag_str(COMPONENT, ctx.get_item("flask_config").integration_name)
    ctx.set_item(name + ".call", span)
    ctx.set_item("current_span", span)


def _on_request_span_modifier(
    ctx, flask_config, request, environ, _HAS_JSON_MIXIN, flask_version, flask_version_str, exception_type
):
    span = ctx.get_item("req_span")
    # Default resource is method and path:
    #   GET /
    #   POST /save
    # We will override this below in `traced_dispatch_request` when we have a `
    # RequestContext` and possibly a url rule
    span.resource = " ".join((request.method, request.path))

    span.set_tag(SPAN_MEASURED_KEY)
    # set analytics sample rate with global config enabled
    sample_rate = flask_config.get_analytics_sample_rate(use_global_config=True)
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

    span.set_tag_str(flask_version, flask_version_str)


def _on_request_span_modifier_post(ctx, flask_config, request, req_body):
    span = ctx.get_item("req_span")
    trace_utils.set_http_meta(
        span,
        flask_config,
        method=request.method,
        url=request.base_url,
        raw_uri=request.url,
        query=request.query_string,
        parsed_query=request.args,
        request_headers=request.headers,
        request_cookies=request.cookies,
        request_body=req_body,
        peer_ip=request.remote_addr,
    )


def _on_start_response_blocked(flask_config, response_headers, status):
    trace_utils.set_http_meta(
        core.get_item("req_span"), flask_config, status_code=status, response_headers=response_headers
    )


def _on_function_context_started_flask(ctx):
    pin = ctx.get_item("pin")
    name = ctx.get_item("name")
    flask_config = ctx.get_item("flask_config")
    kwargs = {"service": trace_utils.int_service(pin, flask_config)}
    for kwarg in ("span_type", "resource"):
        kwarg_value = ctx.get_item(kwarg)
        if kwarg_value:
            kwargs[kwarg] = kwarg_value
    span = pin.tracer.trace(name, **kwargs)
    span.set_tag_str(COMPONENT, flask_config.integration_name)
    signal = ctx.get_item("signal")
    if signal:
        span.set_tag_str("flask.signal", signal)
    ctx.set_item("flask_call", span)


def listen():
    core.on("context.started.wsgi.__call__", _on_context_started)
    core.on("context.started.wsgi.response", _on_response_context_started)
    core.on("wsgi.block.started", _make_block_content)
    core.on("wsgi.request.prepare", _on_request_prepare)
    core.on("wsgi.request.prepared", _on_request_prepared)
    core.on("wsgi.app.success", _on_app_success)
    core.on("wsgi.app.exception", _on_app_exception)
    core.on("wsgi.request.complete", _on_request_complete)
    core.on("wsgi.response.prepared", _on_response_prepared)
    core.on("flask.start_response.pre", _on_start_response_pre)
    core.on("flask.blocked_request_callable", _on_flask_blocked_request)
    core.on("flask.request_call_modifier", _on_request_span_modifier)
    core.on("flask.request_call_modifier.post", _on_request_span_modifier_post)
    core.on("flask.render", _on_flask_render)
    core.on("flask.start_response.blocked", _on_start_response_blocked)
    core.on("context.started.flask._patched_request", _on_traced_request_context_started_flask)
    core.on("context.started.flask.jsonify", _on_jsonify_context_started_flask)
    core.on("context.started.flask.render_template", _on_render_template_context_started_flask)
    core.on("context.started.flask.call", _on_function_context_started_flask)
