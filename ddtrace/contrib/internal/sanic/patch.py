import asyncio
import sys

import sanic
import wrapt
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.wrappers import unwrap as _u


log = get_logger(__name__)

config._add("sanic", dict(_default_service=schematize_service_name("sanic"), distributed_tracing=True))

SANIC_VERSION = (0, 0, 0)
_REQUEST_CONTEXT_ATTR = "__ddtrace_sanic_request_context"


def get_version() -> str:
    return getattr(sanic, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"sanic": ">=20.12.0"}


def _get_request_context(request):
    return getattr(request.ctx, _REQUEST_CONTEXT_ATTR, None)


def _get_request_span(request):
    ctx = _get_request_context(request)
    if ctx is None:
        return None
    return span_from_context(ctx)


def _update_request_event(ctx, response):
    # DEV: response can be a BaseResponse or an exception. Preserve the
    # existing fallback to status 500 when no response status is available.
    event = ctx.event
    event.response_status_code = getattr(response, "status", 500)
    response_headers = getattr(response, "headers", None)
    if response_headers is not None:
        event.response_headers = response_headers


def _finish_request(
    request,
    exc_type=None,
    exc_value=None,
    traceback=None,
):
    ctx = _get_request_context(request)
    if ctx is None:
        return

    try:
        ctx.dispatch_ended_event(exc_type, exc_value, traceback)
    finally:
        setattr(request.ctx, _REQUEST_CONTEXT_ATTR, None)


def _wrap_response_callback(ctx, callback):
    # Only for sanic 20 and older
    # Wrap response callbacks (either sync or async function) to set HTTP
    # response span tags

    @wrapt.function_wrapper
    def wrap_sync(wrapped, instance, args, kwargs):
        r = wrapped(*args, **kwargs)
        response = args[0]
        _update_request_event(ctx, response)
        return r

    @wrapt.function_wrapper
    async def wrap_async(wrapped, instance, args, kwargs):
        r = await wrapped(*args, **kwargs)
        response = args[0]
        _update_request_event(ctx, response)
        return r

    if asyncio.iscoroutinefunction(callback):
        return wrap_async(callback)

    return wrap_sync(callback)


async def patch_request_respond(wrapped, instance, args, kwargs):
    # Only for sanic 21 and newer
    # Wrap the framework response to set HTTP response span tags
    response = await wrapped(*args, **kwargs)
    ctx = _get_request_context(instance)
    if ctx is None:
        return response

    _update_request_event(ctx, response)

    # Sanic 21.9.x does not dispatch `http.lifecycle.response` in `handle_exception`
    #  so we have to handle finishing the span here instead
    if (21, 9, 0) <= SANIC_VERSION < (21, 12, 0) and getattr(instance.ctx, "__dd_span_call_finish", False):
        _finish_request(instance)
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
            log.debug("Failed to convert path parameter value to string", exc_info=True)
            continue
        path = path.replace(value, f"<{key}>")
    return path


async def patch_run_request_middleware(wrapped, instance, args, kwargs):
    # Set span resource from the framework request
    request = args[0]
    span = _get_request_span(request)
    if span is not None:
        span.resource = "{} {}".format(request.method, _get_path(request))
    return await wrapped(*args, **kwargs)


def patch():
    """Patch the instrumented methods."""
    global SANIC_VERSION

    if getattr(sanic, "__datadog_patch", False):
        return
    sanic.__datadog_patch = True

    SANIC_VERSION = tuple(map(int, get_version().split(".")))

    if SANIC_VERSION >= (21, 9, 0):
        _w("sanic", "Sanic.__init__", patch_sanic_init)
        _w(sanic.request, "Request.respond", patch_request_respond)
    else:
        _w("sanic", "Sanic.handle_request", patch_handle_request)
        if SANIC_VERSION >= (21, 0, 0):
            _w("sanic", "Sanic._run_request_middleware", patch_run_request_middleware)
            _w(sanic.request, "Request.respond", patch_request_respond)


def unpatch():
    """Unpatch the instrumented methods."""
    if not getattr(sanic, "__datadog_patch", False):
        return

    if SANIC_VERSION >= (21, 9, 0):
        _u(sanic.Sanic, "__init__")
        _u(sanic.request.Request, "respond")
    else:
        _u(sanic.Sanic, "handle_request")
        if SANIC_VERSION >= (21, 0, 0):
            _u(sanic.Sanic, "_run_request_middleware")
            _u(sanic.request.Request, "respond")

    sanic.__datadog_patch = False


def patch_sanic_init(wrapped, instance, args, kwargs):
    """Wrapper for creating sanic apps to automatically add our signal handlers"""
    wrapped(*args, **kwargs)

    instance.add_signal(sanic_http_lifecycle_handle, "http.lifecycle.handle")
    instance.add_signal(sanic_http_routing_after, "http.routing.after")
    instance.add_signal(sanic_http_lifecycle_exception, "http.lifecycle.exception")
    instance.add_signal(sanic_http_lifecycle_response, "http.lifecycle.response")


async def patch_handle_request(wrapped, instance, args, kwargs):
    """Wrapper for Sanic.handle_request"""

    def unwrap(
        request,
        write_callback=None,
        stream_callback=None,
        **kwargs,
    ):
        return request, write_callback, stream_callback, kwargs

    request, write_callback, stream_callback, new_kwargs = unwrap(*args, **kwargs)

    if request.scheme not in ("http", "https"):
        return await wrapped(*args, **kwargs)

    ctx = _create_sanic_request_context(request)
    try:
        if write_callback is not None:
            new_kwargs["write_callback"] = _wrap_response_callback(ctx, write_callback)
        if stream_callback is not None:
            new_kwargs["stream_callback"] = _wrap_response_callback(ctx, stream_callback)

        return await wrapped(request, **new_kwargs)
    finally:
        exc_type, exc_value, traceback = sys.exc_info()
        _finish_request(request, exc_type, exc_value, traceback)


def _create_sanic_request_context(request):
    """Create the Sanic request event and retain its context until the response."""
    headers = request.headers.copy()
    query_string = request.query_string
    if isinstance(query_string, bytes):
        query_string = query_string.decode()

    url = "{scheme}://{host}{path}".format(scheme=request.scheme, host=request.host, path=request.path)
    event = WebFrameworkRequestEvent(
        http_operation="sanic.request",
        component=config.sanic.integration_name,
        integration_config=config.sanic,
        service=trace_utils.int_service(None, config.sanic),
        request_method=request.method,
        request_url=url,
        request_headers=headers,
        query=query_string,
        request_route=None,
        activate_distributed_headers=True,
        headers_case_sensitive=True,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        if SANIC_VERSION < (21, 0, 0):
            request_span = span_from_context(ctx)
            if request_span is not None:
                request_span.resource = "{} {}".format(request.method, _get_path(request))

        setattr(request.ctx, _REQUEST_CONTEXT_ATTR, ctx)
        return ctx


async def sanic_http_lifecycle_handle(request):
    """Lifecycle signal called when a new request is started."""
    _create_sanic_request_context(request)


async def sanic_http_routing_after(request, route, kwargs, handler):
    """Lifecycle signal called after routing has been resolved."""
    span = _get_request_span(request)
    if not span:
        return

    pattern = route.raw_path
    # Sanic 21.9.0 and newer strip the leading slash from `route.raw_path`
    if not pattern.startswith("/"):
        pattern = "/{}".format(pattern)
    if route.regex:
        pattern = route.pattern

    span.resource = "{} {}".format(request.method, pattern)
    span._set_attribute("sanic.route.name", route.name)


async def sanic_http_lifecycle_response(request, response):
    """Lifecycle signal called when a response is starting.

    Note: This signal does not get called when exceptions occur
          in 21.9.x. The issue was resolved in 21.12.x
    """
    ctx = _get_request_context(request)
    if ctx is None:
        return
    _update_request_event(ctx, response)
    _finish_request(request)


async def sanic_http_lifecycle_exception(request, exception):
    """Lifecycle signal called when an exception occurs."""
    span = _get_request_span(request)
    if not span:
        return

    # Do not attach exception for exceptions not considered as errors
    # ex: Http 400s
    # DEV: We still need to set `__dd_span_call_finish` below
    if not hasattr(exception, "status_code") or config._http_server.is_error_code(exception.status_code):
        ex_type = type(exception)
        ex_tb = getattr(exception, "__traceback__", None)
        span.set_exc_info(ex_type, exception, ex_tb)

    # Sanic 21.9.x does not dispatch `http.lifecycle.response` in `handle_exception`
    #  so we need to indicate to `patch_request_respond` to finish the span
    if (21, 9, 0) <= SANIC_VERSION < (21, 12, 0):
        request.ctx.__dd_span_call_finish = True
