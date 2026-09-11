from typing import Any

from ddtrace._trace._inferred_proxy import _set_inferred_proxy_tags
from ddtrace._trace.span import Span
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.internal import core
from ddtrace.internal.compat import maybe_stringify
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import FLASK_ENDPOINT
from ddtrace.internal.constants import FLASK_RESOURCE_FULL
from ddtrace.internal.constants import FLASK_URL_RULE
from ddtrace.internal.constants import FLASK_VIEW_ARGS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import get_span
from ddtrace.internal.span_bus import span_from_context


log = get_logger(__name__)


def _set_flask_route_tags(request: Any, span: Span) -> None:
    try:
        if not span.get_tag(FLASK_ENDPOINT) and request.endpoint:
            span.resource = " ".join((request.method, request.endpoint))
            span._set_attribute(FLASK_ENDPOINT, request.endpoint)

        if not span.get_tag(FLASK_URL_RULE) and request.url_rule and request.url_rule.rule:
            span.resource = " ".join((request.method, request.url_rule.rule))
            span._set_attribute(FLASK_URL_RULE, request.url_rule.rule)
            if request.script_root:
                span._set_attribute(
                    FLASK_RESOURCE_FULL,
                    " ".join((request.method, request.script_root + request.url_rule.rule)),
                )
    except Exception:
        log.debug('failed to set route tags for "flask.request" span', exc_info=True)


def _set_flask_request_tags(request: Any, span: Span, flask_config: Any) -> None:
    try:
        span._set_attribute(COMPONENT, flask_config.integration_name)
        if span.name.split(".")[-1] == "request":
            span._set_attribute(SPAN_KIND, SpanKind.SERVER)

        _set_flask_route_tags(request, span)
        _set_flask_view_args(request, span, flask_config)
    except Exception:
        log.debug('failed to set tags for "flask.request" span', exc_info=True)


def _set_flask_view_args(request: Any, span: Span, flask_config: Any) -> None:
    try:
        if not span.get_tag(FLASK_VIEW_ARGS) and request.view_args and flask_config.get("collect_view_args"):
            for key, value in request.view_args.items():
                span.set_tag(".".join((FLASK_VIEW_ARGS, key)), value)
            trace_utils.set_http_meta(span, flask_config, request_path_params=request.view_args)
    except Exception:
        log.debug('failed to set view args for "flask.request" span', exc_info=True)


def _update_flask_event_route_fields(event: WebFrameworkRequestEvent, request: Any) -> None:
    endpoint = getattr(request, "endpoint", None)
    if endpoint and FLASK_ENDPOINT not in event.tags:
        event.tags[FLASK_ENDPOINT] = endpoint
        event.resource = " ".join((request.method, endpoint))

    url_rule = getattr(request, "url_rule", None)
    if url_rule and url_rule.rule and FLASK_URL_RULE not in event.tags:
        event.tags[FLASK_URL_RULE] = url_rule.rule
        event.request_route = url_rule.rule
        event.resource = " ".join((request.method, url_rule.rule))
        if request.script_root:
            event.tags[FLASK_RESOURCE_FULL] = " ".join((request.method, request.script_root + url_rule.rule))


def _on_flask_patched_request(ctx: core.ExecutionContext[Any]) -> None:
    request_context = ctx.find_item("request_context")
    request = ctx.find_item("flask_request")
    flask_config = ctx.find_item("flask_config")
    if request_context is None or request is None or flask_config is None:
        return

    request_event = request_context.event
    if not isinstance(request_event, WebFrameworkRequestEvent):
        return

    span = span_from_context(ctx)
    if span is None or span._parent is None:
        return

    _update_flask_event_route_fields(request_event, request)
    _set_flask_request_tags(request, span._parent, flask_config)
    request_span = request_context.get_item("req_span")
    if request_span is not None and request_span is not span._parent:
        _set_flask_route_tags(request, request_span)


def _on_flask_request_call_modifier_post(
    ctx: core.ExecutionContext[Any], flask_config: Any, request: Any, req_body: Any
) -> None:
    event = ctx.event
    span = ctx.get_item("req_span")
    if not isinstance(event, WebFrameworkRequestEvent) or span is None:
        return

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
    ctx.set_item("http_meta_set", True)


def _on_flask_request_call_modifier(
    ctx: core.ExecutionContext[Any],
    flask_config: Any,
    request: Any,
    environ: Any,
    _has_json_mixin: bool,
    flask_version: str,
    flask_version_str: str,
    _exception_type: type[BaseException],
) -> None:
    event = ctx.event
    span = ctx.get_item("req_span")
    if not isinstance(event, WebFrameworkRequestEvent) or span is None:
        return

    event.resource = " ".join((request.method, request.path))
    _update_flask_event_route_fields(event, request)
    event.component = flask_config.integration_name
    setattr(event, "span_kind", SpanKind.SERVER)
    event.measured = True
    event.tags[flask_version] = flask_version_str
    span.resource = event.resource
    span._set_attribute(COMPONENT, flask_config.integration_name)
    span._set_attribute(SPAN_KIND, SpanKind.SERVER)
    span._set_attribute(_SPAN_MEASURED_KEY, 1)
    span._set_attribute(flask_version, flask_version_str)


def _on_flask_start_response_pre(
    request: Any, ctx: core.ExecutionContext[Any], flask_config: Any, status_code: str, headers: Any
) -> None:
    event = ctx.event
    span = ctx.get_item("req_span")
    if not isinstance(event, WebFrameworkRequestEvent) or span is None:
        return

    _update_flask_event_route_fields(event, request)
    _set_flask_view_args(request, span, flask_config)
    for tag_name, tag_value in event.tags.items():
        span._set_attribute(tag_name, tag_value)

    code, _, _ = status_code.partition(" ")
    if not span.get_tag(FLASK_ENDPOINT) and not span.get_tag(FLASK_URL_RULE):
        span.resource = " ".join((request.method, code))
        event.resource = span.resource
    elif event.resource:
        span.resource = event.resource

    trace_utils.set_http_meta(
        span=span,
        integration_config=flask_config,
        method=request.method,
        url=None,
        status_code=code,
        query=None,
        request_headers=None,
        response_headers=headers,
        route=span.get_tag(FLASK_URL_RULE),
        response_cookies=event.response_cookies,
    )
    _set_inferred_proxy_tags(span, code)
    for tk, tv in core.get_item("additional_tags", default=dict()).items():
        span._set_attribute(tk, tv)
    ctx.set_item("http_meta_set", True)


def _on_flask_render(template: Any, flask_config: Any) -> None:
    span = get_span()
    if not span:
        return
    name = getattr(template, "name", None) or flask_config.get("template_default_name")
    if name is not None:
        name = maybe_stringify(name)
        span.resource = name
        span._set_attribute("flask.template_name", name)


core.on("flask._patched_request", _on_flask_patched_request)
core.on("flask.request_call_modifier", _on_flask_request_call_modifier)
core.on("flask.request_call_modifier.post", _on_flask_request_call_modifier_post)
core.on("flask.start_response.pre", _on_flask_start_response_pre)
core.on("flask.render", _on_flask_render)
