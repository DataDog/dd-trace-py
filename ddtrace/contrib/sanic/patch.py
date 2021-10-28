import asyncio

import sanic

import ddtrace
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.ext import SpanTypes
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.pin import Pin
from ddtrace.vendor import wrapt
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from .. import trace_utils
from ...internal.logger import get_logger


log = get_logger(__name__)

config._add("sanic", dict(_default_service="sanic", distributed_tracing=True))

SANIC_PRE_21 = None


def update_span(span, response):
    if isinstance(response, sanic.response.BaseHTTPResponse):
        status_code = response.status
        response_headers = response.headers
    else:
        # invalid response causes ServerError exception which must be handled
        status_code = 500
        response_headers = None
    trace_utils.set_http_meta(span, config.sanic, status_code=status_code, response_headers=response_headers)


def _wrap_response_callback(span, callback):
    # Only for sanic 20 and older
    # Wrap response callbacks (either sync or async function) to set HTTP
    # response span tags

    @wrapt.function_wrapper
    def wrap_sync(wrapped, instance, args, kwargs):
        r = wrapped(*args, **kwargs)
        response = args[0]
        update_span(span, response)
        return r

    @wrapt.function_wrapper
    async def wrap_async(wrapped, instance, args, kwargs):
        r = await wrapped(*args, **kwargs)
        response = args[0]
        update_span(span, response)
        return r

    if asyncio.iscoroutinefunction(callback):
        return wrap_async(callback)

    return wrap_sync(callback)


async def patch_request_respond(wrapped, instance, args, kwargs):
    # Only for sanic 21 and newer
    # Wrap the framework response to set HTTP response span tags
    response = await wrapped(*args, **kwargs)
    pin = Pin._find(instance.ctx)
    if pin is not None and pin.enabled():
        span = pin.tracer.current_span()
        if span is not None:
            update_span(span, response)
    return response


def _get_path(request):
    """Get path and replace path parameter values with names if route exists."""
    path = request.path
    try:
        match_info = request.match_info
    except sanic.exceptions.SanicException:
        return path
    for key, value in match_info.items():
        try:
            value = str(value)
        except Exception:
            # Best effort
            continue
        path = path.replace(value, f"<{key}>")
    return path


async def patch_run_request_middleware(wrapped, instance, args, kwargs):
    # Set span resource from the framework request
    request = args[0]
    pin = Pin._find(request.ctx)
    if pin is not None and pin.enabled():
        span = pin.tracer.current_span()
        if span is not None:
            span.resource = "{} {}".format(request.method, _get_path(request))
    return await wrapped(*args, **kwargs)


def patch():
    """Patch the instrumented methods."""
    global SANIC_PRE_21

    if getattr(sanic, "__datadog_patch", False):
        return
    setattr(sanic, "__datadog_patch", True)

    SANIC_PRE_21 = sanic.__version__[:2] < "21"

    _w("sanic", "Sanic.handle_request", patch_handle_request)
    if not SANIC_PRE_21:
        _w("sanic", "Sanic._run_request_middleware", patch_run_request_middleware)
        _w(sanic.request, "Request.respond", patch_request_respond)


def unpatch():
    """Unpatch the instrumented methods."""
    _u(sanic.Sanic, "handle_request")
    if not SANIC_PRE_21:
        _u(sanic.Sanic, "_run_request_middleware")
        _u(sanic.request.Request, "respond")
    if not getattr(sanic, "__datadog_patch", False):
        return
    setattr(sanic, "__datadog_patch", False)


async def patch_handle_request(wrapped, instance, args, kwargs):
    """Wrapper for Sanic.handle_request"""

    def unwrap(request, write_callback=None, stream_callback=None, **kwargs):
        return request, write_callback, stream_callback, kwargs

    request, write_callback, stream_callback, new_kwargs = unwrap(*args, **kwargs)

    if request.scheme not in ("http", "https"):
        return await wrapped(*args, **kwargs)

    pin = Pin()
    if SANIC_PRE_21:
        # Set span resource from the framework request
        resource = "{} {}".format(request.method, _get_path(request))
    else:
        # The path is not available anymore in 21.x. Get it from
        # the _run_request_middleware instrumented method.
        resource = None
        pin.onto(request.ctx)

    headers = request.headers.copy()

    trace_utils.activate_distributed_headers(ddtrace.tracer, int_config=config.sanic, request_headers=headers)

    with pin.tracer.trace(
        "sanic.request",
        service=trace_utils.int_service(None, config.sanic),
        resource=resource,
        span_type=SpanTypes.WEB,
    ) as span:
        sample_rate = config.sanic.get_analytics_sample_rate(use_global_config=True)
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        method = request.method
        url = "{scheme}://{host}{path}".format(scheme=request.scheme, host=request.host, path=request.path)
        query_string = request.query_string
        if isinstance(query_string, bytes):
            query_string = query_string.decode()
        trace_utils.set_http_meta(
            span, config.sanic, method=method, url=url, query=query_string, request_headers=headers
        )

        if write_callback is not None:
            new_kwargs["write_callback"] = _wrap_response_callback(span, write_callback)
        if stream_callback is not None:
            new_kwargs["stream_callback"] = _wrap_response_callback(span, stream_callback)

        return await wrapped(request, **new_kwargs)
