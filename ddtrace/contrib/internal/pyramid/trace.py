from pyramid.httpexceptions import HTTPException
import pyramid.renderers
from pyramid.settings import asbool
import wrapt

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebRequestEvent
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name

# project
from ddtrace.trace import tracer

from .constants import SETTINGS_DISTRIBUTED_TRACING
from .constants import SETTINGS_SERVICE
from .constants import SETTINGS_TRACE_ENABLED


log = get_logger(__name__)

DD_TWEEN_NAME = "ddtrace.contrib.pyramid:trace_tween_factory"


def trace_pyramid(config):
    config.include("ddtrace.contrib.pyramid")


def includeme(config):
    # Add our tween just before the default exception handler
    config.add_tween(DD_TWEEN_NAME, over=pyramid.tweens.EXCVIEW)
    # ensure we only patch the renderer once.
    if not is_wrapted(pyramid.renderers.RendererHelper.render):
        wrapt.wrap_function_wrapper("pyramid.renderers", "RendererHelper.render", trace_render)


def trace_render(func, instance, args, kwargs):
    # If the request is not traced, we do not trace
    request = kwargs.get("request", {})
    if not request:
        log.debug("No request passed to render, will not be traced")
        return func(*args, **kwargs)

    with tracer.trace("pyramid.render", span_type=SpanTypes.TEMPLATE) as span:
        span._set_tag_str(COMPONENT, config.pyramid.integration_name)

        return func(*args, **kwargs)


def trace_tween_factory(handler, registry):
    # configuration
    settings = registry.settings
    service = settings.get(SETTINGS_SERVICE) or schematize_service_name("pyramid")
    enabled = asbool(settings.get(SETTINGS_TRACE_ENABLED, tracer.enabled))

    # ensure distributed tracing within pyramid settings matches config
    config.pyramid.distributed_tracing_enabled = asbool(settings.get(SETTINGS_DISTRIBUTED_TRACING, True))

    if enabled:
        # make a request tracing function
        def trace_tween(request):
            with core.context_with_event(
                WebRequestEvent(
                    url_operation="pyramid.request",
                    component=config.pyramid.integration_name,
                    service=service,
                    resource="404",
                    integration_config=config.pyramid,
                    activate_distributed_headers=True,
                    request_headers=request.headers,
                    request_method=request.method,
                    request_url=request.path_url,
                    request_query=request.query_string,
                    request_route=request.matched_route.pattern if request.matched_route else None,
                )
            ) as ctx:
                ctx.set_item("headers_case_sensitive", True)
                req_span = ctx.span

                response = None
                status = None
                try:
                    response = handler(request)
                except HTTPException as e:
                    # If the exception is a pyramid HTTPException,
                    # that's still valuable information that isn't necessarily
                    # a 500. For instance, HTTPFound is a 302.
                    # As described in docs, Pyramid exceptions are all valid
                    # response types
                    response = e
                    raise
                except BaseException:
                    status = 500
                    raise
                finally:
                    event: WebRequestEvent = ctx.event

                    # set request tags
                    if request.matched_route:
                        req_span.resource = "{} {}".format(request.method, request.matched_route.name)
                        req_span._set_tag_str("pyramid.route.name", request.matched_route.name)
                        event.request_pattern = request.matched_route.pattern
                    if response:
                        status = response.status_code
                        event.response_headers = response.headers
                    event.response_status_code = status
                return response

        return trace_tween

    # if timing support is not enabled, return the original handler
    return handler
