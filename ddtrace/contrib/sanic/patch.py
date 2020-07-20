import ddtrace
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.ext import SpanTypes, http
from ddtrace.http import store_request_headers, store_response_headers
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.utils.wrappers import unwrap as _u
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

import sanic

from ...internal.logger import get_logger

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

    query_string = None
    if config.sanic.trace_query_string:
        query_string = request.query_string
        if isinstance(query_string, bytes):
            query_string = query_string.decode()
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

    request, write_callback, stream_callback = args

    resource = "{} {}".format(request.method, request.path)

    headers = {k: v for (k, v) in request.headers.items()}

    if config.sanic.distributed_tracing:
        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        if context.trace_id:
            ddtrace.tracer.context_provider.activate(context)

    with ddtrace.tracer.trace(
        "sanic.request", service=config.sanic.service, resource=resource, span_type=SpanTypes.WEB
    ) as span:
        sample_rate = config.sanic.get_analytics_sample_rate(use_global_config=True)
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        tags = _extract_tags_from_request(request=request)
        span.set_tags(tags)

        store_request_headers(headers, span, config.sanic)

        # TODO: add support for tagging status code for streaming response
        def _wrap_write_response(func):
            def traced_write_response(response):
                span.set_tag(http.STATUS_CODE, response.status)
                store_response_headers(response.headers, span, config.sanic)
                return func(response)

            return traced_write_response

        wrapped_args = [request, _wrap_write_response(write_callback), stream_callback]

        return wrapped(*wrapped_args, **kwargs)


def patch_register_middleware(wrapped, instance, args, kwargs):
    """Wrrapper for Sanic.register_middleware"""

    # TODO: wrap middleware
    wrapped(*args, **kwargs)
