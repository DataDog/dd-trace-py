
# stdlib
import time

# 3p
from pyramid.settings import asbool

# project
import ddtrace
from ...ext import http, errors, AppTypes


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
                        span.error = 500 <= response.status_code < 600
                return response
        return trace_tween

    # if timing support is not enabled, return the original handler
    return handler


