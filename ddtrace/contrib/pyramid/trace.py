
# 3p
import pyramid.renderers
from pyramid.settings import asbool
import wrapt

# project
import ddtrace
from ...ext import http, AppTypes


def trace_pyramid(config):
    config.add_tween('ddtrace.contrib.pyramid:trace_tween_factory')
    # ensure we only patch the renderer once.
    if not isinstance(pyramid.renderers.RendererHelper.render, wrapt.ObjectProxy):
        wrapt.wrap_function_wrapper('pyramid.renderers', 'RendererHelper.render', trace_render)

def trace_render(func, instance, args, kwargs):
    # get the tracer from the request or fall back to the global version
    def _tracer(value, system_values, request=None):
        if request:
            span = getattr(request, '_datadog_span', None)
            if span:
                return span.tracer()
        return ddtrace.tracer

    t = _tracer(*args, **kwargs)
    with t.trace('pyramid.render') as span:
        span.span_type = http.TEMPLATE
        return func(*args, **kwargs)

def trace_tween_factory(handler, registry):
    # configuration
    settings = registry.settings
    service = settings.get('datadog_trace_service') or 'pyramid'
    tracer = settings.get('datadog_tracer') or ddtrace.tracer
    enabled = asbool(settings.get('datadog_trace_enabled', tracer.enabled))

    # set the service info
    tracer.set_service_info(
        service=service,
        app="pyramid",
        app_type=AppTypes.web)

    if enabled:
        # make a request tracing function
        def trace_tween(request):
            with tracer.trace('pyramid.request', service=service, resource='404') as span:
                setattr(request, '_datadog_span', span)  # used to find the tracer in templates
                response = None
                try:
                    response = handler(request)
                except Exception:
                    span.set_tag(http.STATUS_CODE, 500)
                    raise
                finally:
                    span.span_type = http.TYPE
                    # set request tags
                    span.set_tag(http.URL, request.path)
                    if request.matched_route:
                        span.resource = request.matched_route.name
                    # set response tags
                    if response:
                        span.set_tag(http.STATUS_CODE, response.status_code)
                        if 500 <= response.status_code < 600:
                            span.error = 1
                return response
        return trace_tween

    # if timing support is not enabled, return the original handler
    return handler
