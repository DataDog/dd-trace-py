from ddtrace import Pin, config
import ddtrace
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.ext import SpanTypes, http
from ddtrace.http import store_request_headers, store_response_headers
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.utils.wrappers import unwrap as _u, iswrapped
from ddtrace.utils.importlib import func_name

import sanic

from ...internal.logger import get_logger
from ...utils.formats import asbool, get_env

log = get_logger(__name__)

config._add("sanic", dict(service=config._get_service(default="sanic"), distributed_tracing=True))


def _extract_tags_from_request(request):
    tags = {}

    http_method = request.method
    if http_method:
        tags[http.METHOD] = http_method
    
    http_url = request.url
    if http_url:
        tags[http.URL] = http_url

    http_version = request.version
    if http_version:
        tags[http.VERSION] = http_version

    # query_string = None
    # if config.sanic.trace_query_string == True: # note: this is configurations that USER has to set so by default its False ..  
    #     query_string = request.query_string
    #     if isinstance(query_string, bytes):
    #         query_string = query_string.decode()
    #     tags[http.QUERY_STRING] = query_string

    query_string = request.query_string
    if query_string:
        tags[http.QUERY_STRING] = query_string

    return tags


def patch():
    """Patch the instrumented methods.
    """
    if getattr(sanic, "__datadog_patch", False):
        return
    setattr(sanic, "__datadog_patch", True)
    _w("sanic", "Sanic.handle_request", patch_handle_request)
    _w("sanic", "Sanic.register_middleware", patch_register_middleware)


def unpatch():
    """Unpatch the instrumented methods.
    """
    _u(sanic.Sanic, "handle_request")
    _u(sanic.Sanic, "register_middleware")


def patch_handle_request(wrapped, instance, args, kwargs):
    """Wrapper for Sanic.handle_request"""

    request = args[0]
    resource = "{} {}".format(request.method, request.path)

    headers = {k: v for (k, v) in request.headers.items()}

    if config.sanic.distributed_tracing:
        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        if context.trace_id:
            ddtrace.tracer.context_provider.activate(context)

    with ddtrace.tracer.trace("sanic.request", service=config.sanic.service, resource=resource, span_type=SpanTypes.WEB) as span:
        sample_rate = config.sanic.get_analytics_sample_rate(use_global_config=True)
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        tags = _extract_tags_from_request(request=request)
        span.set_tags(tags)

        store_request_headers(headers, span, config.sanic)

        return wrapped(*args, **kwargs)


def patch_register_middleware(wrapped, instance, args, kwargs):
    """Wrrapper for Sanic.register_middleware"""

    # TODO: wrap middleware
    wrapped(*args, **kwargs)
