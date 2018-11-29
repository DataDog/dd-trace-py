# 3p
import logging
import pyramid.renderers
from pyramid.settings import asbool
from pyramid.httpexceptions import HTTPException
import wrapt

# project
import ddtrace
from ...ext import http, AppTypes
from ...propagation.http import HTTPPropagator
from .constants import (
    SETTINGS_TRACER,
    SETTINGS_SERVICE,
    SETTINGS_TRACE_ENABLED,
    SETTINGS_DISTRIBUTED_TRACING,
)


log = logging.getLogger(__name__)

DD_TWEEN_NAME = 'ddtrace.contrib.pyramid:trace_tween_factory'
DD_SPAN = '_datadog_span'


def trace_pyramid(config):
    config.include('ddtrace.contrib.pyramid')


def includeme(config):
    # Add our tween just before the default exception handler
    config.add_tween(DD_TWEEN_NAME, over=pyramid.tweens.EXCVIEW)
    # ensure we only patch the renderer once.
    if not isinstance(pyramid.renderers.RendererHelper.render, wrapt.ObjectProxy):
        wrapt.wrap_function_wrapper('pyramid.renderers', 'RendererHelper.render', trace_render)


def trace_render(func, instance, args, kwargs):
    # If the request is not traced, we do not trace
    request = kwargs.get('request', {})
    if not request:
        log.debug("No request passed to render, will not be traced")
        return func(*args, **kwargs)
    span = getattr(request, DD_SPAN, None)
    if not span:
        log.debug("No span found in request, will not be traced")
        return func(*args, **kwargs)

    tracer = span.tracer()
    with tracer.trace('pyramid.render') as span:
        span.span_type = http.TEMPLATE
        return func(*args, **kwargs)


def trace_tween_factory(handler, registry):
    # configuration
    settings = registry.settings
    service = settings.get(SETTINGS_SERVICE) or 'pyramid'
    tracer = settings.get(SETTINGS_TRACER) or ddtrace.tracer
    enabled = asbool(settings.get(SETTINGS_TRACE_ENABLED, tracer.enabled))
    distributed_tracing = asbool(settings.get(SETTINGS_DISTRIBUTED_TRACING, False))

    # set the service info
    tracer.set_service_info(
        service=service,
        app="pyramid",
        app_type=AppTypes.web)

    if enabled:
        # make a request tracing function
        def trace_tween(request):
            if distributed_tracing:
                propagator = HTTPPropagator()
                context = propagator.extract(request.headers)
                # only need to active the new context if something was propagated
                if context.trace_id:
                    tracer.context_provider.activate(context)
            with tracer.trace('pyramid.request', service=service, resource='404') as span:
                setattr(request, DD_SPAN, span)  # used to find the tracer in templates
                response = None
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
                    span.set_tag(http.STATUS_CODE, 500)
                    raise
                finally:
                    span.span_type = http.TYPE
                    # set request tags
                    span.set_tag(http.URL, request.path)
                    span.set_tag(http.METHOD, request.method)
                    if request.matched_route:
                        span.resource = '{} {}'.format(request.method, request.matched_route.name)
                        span.set_tag('pyramid.route.name', request.matched_route.name)
                    # set response tags
                    if response:
                        span.set_tag(http.STATUS_CODE, response.status_code)
                        if 500 <= response.status_code < 600:
                            span.error = 1
                return response
        return trace_tween

    # if timing support is not enabled, return the original handler
    return handler
