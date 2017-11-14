
# 3p
import logging
import pyramid.renderers
from pyramid.settings import asbool
import wrapt

# project
import ddtrace
from ...ext import http, AppTypes
from .constants import SETTINGS_SERVICE, SETTINGS_TRACE_ENABLED, SETTINGS_TRACER

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
    request = kwargs.pop('request', {})
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

    # set the service info
    tracer.set_service_info(
        service=service,
        app="pyramid",
        app_type=AppTypes.web)

    if enabled:
        # make a request tracing function
        def trace_tween(request):
            with tracer.trace('pyramid.request', service=service, resource='404') as span:
                setattr(request, DD_SPAN, span)  # used to find the tracer in templates
                response = None
                try:
                    response = handler(request)
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
