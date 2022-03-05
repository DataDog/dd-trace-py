import os
import typing

import httpx
from six import ensure_binary
from six import ensure_text

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib.trace_utils import distributed_tracing_enabled
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.pin import Pin
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


if typing.TYPE_CHECKING:
    from ddtrace import Span
    from ddtrace.vendor.wrapt import BoundFunctionWrapper

config._add(
    "httpx",
    {
        "distributed_tracing": asbool(os.getenv("DD_HTTPX_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_HTTPX_SPLIT_BY_DOMAIN", default=False)),
    },
)


def _url_to_str(url):
    # type: (httpx.URL) -> str
    """
    Helper to convert the httpx.URL parts from bytes to a str
    """
    scheme, host, port, raw_path = url.raw
    url = scheme + b"://" + host
    if port is not None:
        url += b":" + ensure_binary(str(port))
    url += raw_path
    return ensure_text(url)


def _get_service_name(pin, request):
    # type: (Pin, httpx.Request) -> typing.Text
    if config.httpx.split_by_domain:
        if hasattr(request.url, "netloc"):
            return ensure_text(request.url.netloc, errors="backslashreplace")
        else:
            service = ensure_binary(request.url.host)
            if request.url.port:
                service += b":" + ensure_binary(str(request.url.port))
            return ensure_text(service, errors="backslashreplace")
    return ext_service(pin, config.httpx)


def _init_span(span, request):
    # type: (Span, httpx.Request) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    if distributed_tracing_enabled(config.httpx):
        HTTPPropagator.inject(span.context, request.headers)

    sample_rate = config.httpx.get_analytics_sample_rate(use_global_config=True)
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _set_span_meta(span, request, response):
    # type: (Span, httpx.Request, httpx.Response) -> None
    set_http_meta(
        span,
        config.httpx,
        method=request.method,
        url=_url_to_str(request.url),
        status_code=response.status_code if response else None,
        query=request.url.query,
        request_headers=request.headers,
        response_headers=response.headers if response else None,
    )


async def _wrapped_async_send(
    wrapped,  # type: BoundFunctionWrapper
    instance,  # type: httpx.AsyncClient
    args,  # type: typing.Tuple[httpx.Request],
    kwargs,  # type: typing.Dict[typing.Str, typing.Any]
):
    # type: (...) -> typing.Coroutine[None, None, httpx.Response]
    req = get_argument_value(args, kwargs, 0, "request")

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    with pin.tracer.trace("http.request", service=_get_service_name(pin, req), span_type=SpanTypes.HTTP) as span:
        _init_span(span, req)
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            _set_span_meta(span, req, resp)


def _wrapped_sync_send(
    wrapped,  # type: BoundFunctionWrapper
    instance,  # type: httpx.AsyncClient
    args,  # type: typing.Tuple[httpx.Request]
    kwargs,  # type: typing.Dict[typing.Str, typing.Any]
):
    # type: (...) -> httpx.Response
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    req = get_argument_value(args, kwargs, 0, "request")

    with pin.tracer.trace("http.request", service=_get_service_name(pin, req), span_type=SpanTypes.HTTP) as span:
        _init_span(span, req)
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            _set_span_meta(span, req, resp)


def patch():
    # type: () -> None
    if getattr(httpx, "_datadog_patch", False):
        return

    setattr(httpx, "_datadog_patch", True)

    _w(httpx.AsyncClient, "send", _wrapped_async_send)
    _w(httpx.Client, "send", _wrapped_sync_send)

    pin = Pin()
    pin.onto(httpx.AsyncClient)
    pin.onto(httpx.Client)


def unpatch():
    # type: () -> None
    if not getattr(httpx, "_datadog_patch", False):
        return

    setattr(httpx, "_datadog_patch", False)

    _u(httpx.AsyncClient, "send")
    _u(httpx.Client, "send")
