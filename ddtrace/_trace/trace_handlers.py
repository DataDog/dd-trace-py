import functools
import re
import sys
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace._trace.utils import extract_DD_context_from_messages
from ddtrace._trace.utils import set_botocore_patched_api_call_span_tags as set_patched_api_call_span_tags
from ddtrace._trace.utils import set_botocore_response_metadata_tags
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.contrib.trace_utils import _get_request_header_user_agent
from ddtrace.contrib.trace_utils import _set_url_tag
from ddtrace.ext import SpanKind
from ddtrace.ext import db
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
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import http as http_utils
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor import wrapt


log = get_logger(__name__)


class _TracedIterable(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, parent_span, wrapped_is_iterator=False):
        self._self_wrapped_is_iterator = wrapped_is_iterator
        if self._self_wrapped_is_iterator:
            super(_TracedIterable, self).__init__(wrapped)
            self._wrapped_iterator = iter(wrapped)
        else:
            super(_TracedIterable, self).__init__(iter(wrapped))
        self._self_span = span
        self._self_parent_span = parent_span
        self._self_span_finished = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            if self._self_wrapped_is_iterator:
                return next(self._wrapped_iterator)
            else:
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


def _get_parameters_for_new_span_directly_from_context(ctx: core.ExecutionContext) -> Dict[str, str]:
    span_kwargs = {}
    for parameter_name in {"span_type", "resource", "service", "child_of", "activate"}:
        parameter_value = ctx.get_item(parameter_name, traverse=False)
        if parameter_value:
            span_kwargs[parameter_name] = parameter_value
    return span_kwargs


def _start_span(ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> Span:
    span_kwargs = _get_parameters_for_new_span_directly_from_context(ctx)
    call_trace = ctx.get_item("call_trace", call_trace)
    tracer = (ctx.get_item("middleware") or ctx["pin"]).tracer
    distributed_headers_config = ctx.get_item("distributed_headers_config")
    if distributed_headers_config:
        trace_utils.activate_distributed_headers(
            tracer, int_config=distributed_headers_config, request_headers=ctx["distributed_headers"]
        )
    distributed_context = ctx.get_item("distributed_context", traverse=True)
    if distributed_context and not call_trace:
        span_kwargs["child_of"] = distributed_context
    span_kwargs.update(kwargs)
    span = (tracer.trace if call_trace else tracer.start_span)(ctx["span_name"], **span_kwargs)
    for tk, tv in ctx.get_item("tags", dict()).items():
        span.set_tag_str(tk, tv)
    call_keys = ctx.get_item("call_key", "call")
    if isinstance(call_keys, str):
        call_keys = [call_keys]
    for call_key in call_keys:
        ctx.set_item(call_key, span)
    return span


def _on_traced_request_context_started_flask(ctx):
    current_span = ctx["pin"].tracer.current_span()
    if not ctx["pin"].enabled or not current_span:
        ctx.set_item(ctx["call_key"], nullcontext())
        return

    ctx.set_item("current_span", current_span)
    flask_config = ctx["flask_config"]
    _set_flask_request_tags(ctx["flask_request"], current_span, flask_config)
    request_span = _start_span(ctx)
    request_span._ignore_exception(ctx.get_item("ignored_exception_type"))


def _maybe_start_http_response_span(ctx: core.ExecutionContext) -> None:
    request_span = ctx["request_span"]
    middleware = ctx["middleware"]
    status_code, status_msg = ctx["status"].split(" ", 1)
    trace_utils.set_http_meta(
        request_span, middleware._config, status_code=status_code, response_headers=ctx["environ"]
    )
    if ctx.get_item("start_span", False):
        request_span.set_tag_str(http.STATUS_MSG, status_msg)
        _start_span(
            ctx,
            call_trace=False,
            child_of=ctx["parent_call"],
            activate=True,
        )


def _use_html(headers) -> bool:
    """decide if the response should be html or json.

    Add support for quality values in the Accept header.
    """
    ctype = headers.get("Accept", headers.get("accept", ""))
    if not ctype:
        return False
    html_score = 0.0
    json_score = 0.0
    ctypes = ctype.split(",")
    for ct in ctypes:
        if len(ct) > 128:
            # ignore long (and probably malicious) headers to avoid performances issues
            continue
        m = re.match(r"([^/;]+/[^/;]+)(?:;q=([01](?:\.\d*)?))?", ct.strip())
        if m:
            if m.group(1) == "text/html":
                html_score = max(html_score, min(1.0, float(1.0 if m.group(2) is None else m.group(2))))
            elif m.group(1) == "text/*":
                html_score = max(html_score, min(1.0, float(0.2 if m.group(2) is None else m.group(2))))
            elif m.group(1) == "application/json":
                json_score = max(json_score, min(1.0, float(1.0 if m.group(2) is None else m.group(2))))
            elif m.group(1) == "application/*":
                json_score = max(json_score, min(1.0, float(0.2 if m.group(2) is None else m.group(2))))
    return html_score > json_score


def _ctype_from_headers(block_config, headers) -> str:
    """compute MIME type of the blocked response."""
    desired_type = block_config.get("type", "auto")
    if desired_type == "auto":
        return "text/html" if _use_html(headers) else "application/json"
    else:
        return "text/html" if block_config["type"] == "html" else "application/json"


def _wsgi_make_block_content(ctx, construct_url):
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
        ctype = _ctype_from_headers(block_config, headers)
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
    resp_headers.append(("Content-Length", str(len(content))))
    return status, resp_headers, content


def _asgi_make_block_content(ctx, url):
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
        resp_headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"location", block_config.get("location", "").encode()),
        ]
    else:
        ctype = _ctype_from_headers(block_config, headers)
        content = http_utils._get_blocked_template(ctype).encode("UTF-8")
        # ctype = f"{ctype}; charset=utf-8" can be considered at some point
        resp_headers = [(b"content-type", ctype.encode())]
    status = block_config.get("status_code", 403)
    try:
        req_span.set_tag_str(RESPONSE_HEADERS + ".content-length", str(len(content)))
        if ctype is not None:
            req_span.set_tag_str(RESPONSE_HEADERS + ".content-type", ctype)
        req_span.set_tag_str(http.STATUS_CODE, str(status))
        query_string = environ.get("QUERY_STRING")
        _set_url_tag(middleware.integration_config, req_span, url, query_string)
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
    resp_headers.append((b"Content-Length", str(len(content)).encode()))
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


def _on_app_success(ctx, closing_iterable):
    app_span = ctx.get_item("app_span")
    middleware = ctx.get_item("middleware")
    modifier = (
        middleware._application_call_modifier
        if hasattr(middleware, "_application_call_modifier")
        else middleware._application_span_modifier
    )
    modifier(app_span, ctx.get_item("environ"), closing_iterable)
    app_span.finish()


def _on_app_exception(ctx):
    req_span = ctx.get_item("req_span")
    app_span = ctx.get_item("app_span")
    req_span.set_exc_info(*sys.exc_info())
    app_span.set_exc_info(*sys.exc_info())
    app_span.finish()
    req_span.finish()


def _on_request_complete(ctx, closing_iterable, app_is_iterator):
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
    modifier(resp_span, closing_iterable)

    return _TracedIterable(closing_iterable, resp_span, req_span, wrapped_is_iterator=app_is_iterator)


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


def _set_flask_request_tags(request, span, flask_config):
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
    _set_flask_request_tags(request, span, flask_config)
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
    try:
        raw_uri = ctx.get_item("wsgi.construct_url")(ctx.get_item("environ"))
    except Exception:
        raw_uri = request.url
    trace_utils.set_http_meta(
        span,
        flask_config,
        method=request.method,
        url=request.base_url,
        raw_uri=raw_uri,
        query=request.query_string,
        parsed_query=request.args,
        request_headers=request.headers,
        request_cookies=request.cookies,
        request_body=req_body,
        peer_ip=request.remote_addr,
    )


def _on_start_response_blocked(ctx, flask_config, response_headers, status):
    trace_utils.set_http_meta(ctx["req_span"], flask_config, status_code=status, response_headers=response_headers)


def _on_traced_get_response_pre(_, ctx: core.ExecutionContext, request, before_request_tags):
    before_request_tags(ctx["pin"], ctx["call"], request)
    ctx["call"]._metrics[SPAN_MEASURED_KEY] = 1


def _on_django_finalize_response_pre(ctx, after_request_tags, request, response):
    # DEV: Always set these tags, this is where `span.resource` is set
    span = ctx["call"]
    after_request_tags(ctx["pin"], span, request, response)
    trace_utils.set_http_meta(span, ctx["distributed_headers_config"], route=span.get_tag("http.route"))


def _on_django_start_response(
    ctx, request, extract_body: Callable, remake_body: Callable, query: str, uri: str, path: Optional[Dict[str, str]]
):
    parsed_query = request.GET
    body = extract_body(request)
    remake_body(request)

    trace_utils.set_http_meta(
        ctx["call"],
        ctx["distributed_headers_config"],
        method=request.method,
        query=query,
        raw_uri=uri,
        request_path_params=path,
        parsed_query=parsed_query,
        request_body=body,
        request_cookies=request.COOKIES,
    )


def _on_django_cache(ctx: core.ExecutionContext, rowcount: int):
    ctx["call"].set_metric(db.ROWCOUNT, rowcount)


def _on_django_func_wrapped(_unused1, _unused2, _unused3, ctx, ignored_excs):
    if ignored_excs:
        for exc in ignored_excs:
            ctx["call"]._ignore_exception(exc)


def _on_django_process_exception(ctx: core.ExecutionContext, should_set_traceback: bool):
    if should_set_traceback:
        ctx["call"].set_traceback()


def _on_django_block_request(ctx: core.ExecutionContext, metadata: Dict[str, str], django_config, url: str, query: str):
    for tk, tv in metadata.items():
        ctx["call"].set_tag_str(tk, tv)
    _set_url_tag(django_config, ctx["call"], url, query)


def _on_django_after_request_headers_post(
    request_headers,
    response_headers,
    span: Span,
    django_config,
    request,
    url,
    raw_uri,
    status,
    response_cookies,
):
    trace_utils.set_http_meta(
        span,
        django_config,
        method=request.method,
        url=url,
        raw_uri=raw_uri,
        status_code=status,
        query=request.META.get("QUERY_STRING", None),
        parsed_query=request.GET,
        request_headers=request_headers,
        response_headers=response_headers,
        request_cookies=request.COOKIES,
        request_path_params=request.resolver_match.kwargs if request.resolver_match is not None else None,
        peer_ip=core.get_item("http.request.remote_ip", span=span),
        headers_are_case_sensitive=bool(core.get_item("http.request.headers_case_sensitive", span=span)),
        response_cookies=response_cookies,
    )


def _on_botocore_patched_api_call_started(ctx):
    span = ctx.get_item(ctx.get_item("call_key"))
    set_patched_api_call_span_tags(
        span,
        ctx.get_item("instance"),
        ctx.get_item("args"),
        ctx.get_item("params"),
        ctx.get_item("endpoint_name"),
        ctx.get_item("operation"),
    )

    # we need this since we may have ran the wrapped operation before starting the span
    # we need to ensure the span start time is correct
    start_ns = ctx.get_item("start_ns")
    if start_ns is not None and ctx.get_item("func_run"):
        span.start_ns = start_ns


def _on_botocore_patched_api_call_exception(ctx, response, exception_type, is_error_code_fn):
    span = ctx.get_item(ctx.get_item("call_key"))
    # `ClientError.response` contains the result, so we can still grab response metadata
    set_botocore_response_metadata_tags(span, response, is_error_code_fn=is_error_code_fn)

    # If we have a status code, and the status code is not an error,
    #   then ignore the exception being raised
    status_code = span.get_tag(http.STATUS_CODE)
    if status_code and not is_error_code_fn(int(status_code)):
        span._ignore_exception(exception_type)


def _on_botocore_patched_api_call_success(ctx, response):
    set_botocore_response_metadata_tags(ctx.get_item(ctx.get_item("call_key")), response)


def _on_botocore_trace_context_injection_prepared(
    ctx, cloud_service, schematization_function, injection_function, trace_operation
):
    endpoint_name = ctx.get_item("endpoint_name")
    if cloud_service is not None:
        span = ctx.get_item(ctx["call_key"])
        inject_kwargs = dict(endpoint_service=endpoint_name) if cloud_service == "sns" else dict()
        schematize_kwargs = dict(cloud_provider="aws", cloud_service=cloud_service)
        if endpoint_name != "lambda":
            schematize_kwargs["direction"] = SpanDirection.OUTBOUND
        try:
            injection_function(ctx, **inject_kwargs)
            span.name = schematization_function(trace_operation, **schematize_kwargs)
        except Exception:
            log.warning("Unable to inject trace context", exc_info=True)


def _on_botocore_kinesis_update_record(ctx, stream, data_obj: Dict, record, inject_trace_context):
    if inject_trace_context:
        if "_datadog" not in data_obj:
            data_obj["_datadog"] = {}
        HTTPPropagator.inject(ctx[ctx["call_key"]].context, data_obj["_datadog"])


def _on_botocore_update_messages(ctx, span, _, trace_data, __, message=None):
    context = span.context if span else ctx[ctx["call_key"]].context
    HTTPPropagator.inject(context, trace_data)


def _on_botocore_patched_bedrock_api_call_started(ctx, request_params):
    span = ctx[ctx["call_key"]]
    integration = ctx["bedrock_integration"]
    span.set_tag_str("bedrock.request.model_provider", ctx["model_provider"])
    span.set_tag_str("bedrock.request.model", ctx["model_name"])
    for k, v in request_params.items():
        if k == "prompt":
            if integration.is_pc_sampled_span(span):
                v = integration.trunc(str(v))
        span.set_tag_str("bedrock.request.{}".format(k), str(v))
        if k == "n":
            ctx.set_item("num_generations", str(v))


def _on_botocore_patched_bedrock_api_call_exception(ctx, exc_info):
    span = ctx[ctx["call_key"]]
    span.set_exc_info(*exc_info)
    prompt = ctx["prompt"]
    model_name = ctx["model_name"]
    integration = ctx["bedrock_integration"]
    if integration.is_pc_sampled_llmobs(span) and "embed" not in model_name:
        integration.llmobs_set_tags(span, formatted_response=None, prompt=prompt, err=True)
    span.finish()


def _on_botocore_patched_bedrock_api_call_success(ctx, reqid, latency, input_token_count, output_token_count):
    span = ctx[ctx["call_key"]]
    span.set_tag_str("bedrock.response.id", reqid)
    span.set_tag_str("bedrock.response.duration", latency)
    span.set_tag_str("bedrock.usage.prompt_tokens", input_token_count)
    span.set_tag_str("bedrock.usage.completion_tokens", output_token_count)


def _on_botocore_bedrock_process_response(
    ctx: core.ExecutionContext,
    formatted_response: Dict[str, Any],
    metadata: Dict[str, Any],
    body: Dict[str, List[Dict]],
    should_set_choice_ids: bool,
) -> None:
    text = formatted_response["text"]
    span = ctx[ctx["call_key"]]
    model_name = ctx["model_name"]
    if should_set_choice_ids:
        for i in range(len(text)):
            span.set_tag_str("bedrock.response.choices.{}.id".format(i), str(body["generations"][i]["id"]))
    integration = ctx["bedrock_integration"]
    if metadata is not None:
        for k, v in metadata.items():
            span.set_tag_str("bedrock.{}".format(k), str(v))
    if "embed" in model_name:
        span.set_metric("bedrock.response.embedding_length", len(formatted_response["text"][0]))
        span.finish()
        return
    for i in range(len(formatted_response["text"])):
        if integration.is_pc_sampled_span(span):
            span.set_tag_str(
                "bedrock.response.choices.{}.text".format(i),
                integration.trunc(str(formatted_response["text"][i])),
            )
        span.set_tag_str(
            "bedrock.response.choices.{}.finish_reason".format(i), str(formatted_response["finish_reason"][i])
        )
    if integration.is_pc_sampled_llmobs(span):
        integration.llmobs_set_tags(span, formatted_response=formatted_response, prompt=ctx["prompt"])
    span.finish()


def _on_botocore_sqs_recvmessage_post(
    ctx: core.ExecutionContext, _, result: Dict, propagate: bool, message_parser: Callable
) -> None:
    if result is not None and "Messages" in result and len(result["Messages"]) >= 1:
        ctx.set_item("message_received", True)
        if propagate:
            ctx.set_safe("distributed_context", extract_DD_context_from_messages(result["Messages"], message_parser))


def _on_botocore_kinesis_getrecords_post(
    ctx: core.ExecutionContext,
    _,
    __,
    ___,
    ____,
    result,
    propagate: bool,
    message_parser: Callable,
):
    if result is not None and "Records" in result and len(result["Records"]) >= 1:
        ctx.set_item("message_received", True)
        if propagate:
            ctx.set_item("distributed_context", extract_DD_context_from_messages(result["Records"], message_parser))


def _on_redis_command_post(ctx: core.ExecutionContext, rowcount):
    if rowcount is not None:
        ctx[ctx["call_key"]].set_metric(db.ROWCOUNT, rowcount)


def listen():
    core.on("wsgi.block.started", _wsgi_make_block_content, "status_headers_content")
    core.on("asgi.block.started", _asgi_make_block_content, "status_headers_content")
    core.on("wsgi.request.prepare", _on_request_prepare)
    core.on("wsgi.request.prepared", _on_request_prepared)
    core.on("wsgi.app.success", _on_app_success)
    core.on("wsgi.app.exception", _on_app_exception)
    core.on("wsgi.request.complete", _on_request_complete, "traced_iterable")
    core.on("wsgi.response.prepared", _on_response_prepared)
    core.on("flask.start_response.pre", _on_start_response_pre)
    core.on("flask.blocked_request_callable", _on_flask_blocked_request)
    core.on("flask.request_call_modifier", _on_request_span_modifier)
    core.on("flask.request_call_modifier.post", _on_request_span_modifier_post)
    core.on("flask.render", _on_flask_render)
    core.on("flask.start_response.blocked", _on_start_response_blocked)
    core.on("context.started.wsgi.response", _maybe_start_http_response_span)
    core.on("context.started.flask._patched_request", _on_traced_request_context_started_flask)
    core.on("django.traced_get_response.pre", _on_traced_get_response_pre)
    core.on("django.finalize_response.pre", _on_django_finalize_response_pre)
    core.on("django.start_response", _on_django_start_response)
    core.on("django.cache", _on_django_cache)
    core.on("django.func.wrapped", _on_django_func_wrapped)
    core.on("django.process_exception", _on_django_process_exception)
    core.on("django.block_request_callback", _on_django_block_request)
    core.on("django.after_request_headers.post", _on_django_after_request_headers_post)
    core.on("botocore.patched_api_call.exception", _on_botocore_patched_api_call_exception)
    core.on("botocore.patched_api_call.success", _on_botocore_patched_api_call_success)
    core.on("botocore.patched_kinesis_api_call.success", _on_botocore_patched_api_call_success)
    core.on("botocore.patched_kinesis_api_call.exception", _on_botocore_patched_api_call_exception)
    core.on("botocore.prep_context_injection.post", _on_botocore_trace_context_injection_prepared)
    core.on("botocore.patched_api_call.started", _on_botocore_patched_api_call_started)
    core.on("botocore.patched_kinesis_api_call.started", _on_botocore_patched_api_call_started)
    core.on("botocore.patched_kinesis_api_call.exception", _on_botocore_patched_api_call_exception)
    core.on("botocore.patched_kinesis_api_call.success", _on_botocore_patched_api_call_success)
    core.on("botocore.kinesis.update_record", _on_botocore_kinesis_update_record)
    core.on("botocore.patched_sqs_api_call.started", _on_botocore_patched_api_call_started)
    core.on("botocore.patched_sqs_api_call.exception", _on_botocore_patched_api_call_exception)
    core.on("botocore.patched_sqs_api_call.success", _on_botocore_patched_api_call_success)
    core.on("botocore.sqs_sns.update_messages", _on_botocore_update_messages)
    core.on("botocore.patched_stepfunctions_api_call.started", _on_botocore_patched_api_call_started)
    core.on("botocore.patched_stepfunctions_api_call.exception", _on_botocore_patched_api_call_exception)
    core.on("botocore.stepfunctions.update_messages", _on_botocore_update_messages)
    core.on("botocore.eventbridge.update_messages", _on_botocore_update_messages)
    core.on("botocore.client_context.update_messages", _on_botocore_update_messages)
    core.on("botocore.patched_bedrock_api_call.started", _on_botocore_patched_bedrock_api_call_started)
    core.on("botocore.patched_bedrock_api_call.exception", _on_botocore_patched_bedrock_api_call_exception)
    core.on("botocore.patched_bedrock_api_call.success", _on_botocore_patched_bedrock_api_call_success)
    core.on("botocore.bedrock.process_response", _on_botocore_bedrock_process_response)
    core.on("botocore.sqs.ReceiveMessage.post", _on_botocore_sqs_recvmessage_post)
    core.on("botocore.kinesis.GetRecords.post", _on_botocore_kinesis_getrecords_post)
    core.on("redis.async_command.post", _on_redis_command_post)
    core.on("redis.command.post", _on_redis_command_post)

    for context_name in (
        "flask.call",
        "flask.jsonify",
        "flask.render_template",
        "wsgi.__call__",
        "django.traced_get_response",
        "django.cache",
        "django.template.render",
        "django.process_exception",
        "django.func.wrapped",
        "botocore.instrumented_api_call",
        "botocore.instrumented_lib_function",
        "botocore.patched_kinesis_api_call",
        "botocore.patched_sqs_api_call",
        "botocore.patched_stepfunctions_api_call",
        "botocore.patched_bedrock_api_call",
        "redis.command",
    ):
        core.on(f"context.started.start_span.{context_name}", _start_span)


listen()
