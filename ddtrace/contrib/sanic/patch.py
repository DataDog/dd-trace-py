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
    tags[http.METHOD] = request.method

    url = '{scheme}://{host}{path}'.format(scheme=request.scheme, host=request.host, path=request.path)
    tags[http.URL] = url

    query_string = None
    if config.sanic.trace_query_string:
        query_string = request.query_string
        if isinstance(query_string, bytes):
            query_string = query_string.decode()
        tags[http.QUERY_STRING] = query_string
        tags[http.URL] = request.url

    return tags


def _update_span_from_response(span, response):
    span.set_tag(http.STATUS_CODE, response.status)
    if 500 <= response.status < 600:
        span.error = 1
    store_response_headers(response.headers, span, config.sanic)


def patch():
    """Patch the instrumented methods.
    """
    if getattr(sanic, "__datadog_patch", False):
        return
    setattr(sanic, "__datadog_patch", True)
    _w("sanic", "Sanic.handle_request", patch_handle_request)


def unpatch():
    """Unpatch the instrumented methods.
    """
    _u(sanic.Sanic, "handle_request")
    if not getattr(sanic, '__datadog_patch', False):
        return
    setattr(sanic, '__datadog_patch', False)


def patch_handle_request(wrapped, instance, args, kwargs):
    """Wrapper for Sanic.handle_request"""

    request, write_callback, stream_callback = args

    resource = "{} {}".format(request.method, request.path)

    headers = request.headers.copy()

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


        # wrap response callbacks to set span tags based on response
        def _wrap_sync_response_callback(func):
            def traced_func(response):
                r = func(response)
                _update_span_from_response(span, response)
                return r

            return traced_func

        def _wrap_async_response_callback(func):
            async def traced_func(response):
                if isinstance(response, sanic.response.StreamingHTTPResponse):
                    r = await func(response)
                    _update_span_from_response(span, response)
                    return r

            return traced_func

        if write_callback is not None:
            write_callback = _wrap_sync_response_callback(write_callback)
        if stream_callback is not None:
            stream_callback = _wrap_async_response_callback(stream_callback)

        return wrapped(request, write_callback, stream_callback, **kwargs)
