import functools
import http.client as httplib
import os
import sys
from typing import Dict
from urllib import parse

import wrapt

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Pin


span_name = "http.client.request"
span_name = schematize_url_operation(span_name, protocol="http", direction=SpanDirection.OUTBOUND)

log = get_logger(__name__)


config._add(
    "httplib",
    {
        "distributed_tracing": asbool(os.getenv("DD_HTTPLIB_DISTRIBUTED_TRACING", default=True)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
    },
)


def get_version():
    # type: () -> str
    return ""


def _supported_versions() -> Dict[str, str]:
    return {"http.client": "*"}


def _wrap_init(func, instance, args, kwargs):
    Pin(service=None, _config=config.httplib).onto(instance)
    return func(*args, **kwargs)


def _wrap_getresponse(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    resp = None
    try:
        resp = func(*args, **kwargs)
        return resp
    finally:
        try:
            # Get the span attached to this instance, if available
            span = getattr(instance, "_datadog_span", None)
            if span:
                if resp:
                    trace_utils.set_http_meta(
                        span, config.httplib, status_code=resp.status, response_headers=resp.getheaders()
                    )

                span.finish()
                delattr(instance, "_datadog_span")
        except Exception:
            log.debug("error applying request tags", exc_info=True)


def _call_asm_wrap(func, instance, *args, **kwargs):
    from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_request_asm

    _wrap_request_asm(func, instance, args, kwargs)


def _wrap_request(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    if asm_config._iast_enabled or (asm_config._asm_enabled and asm_config._ep_enabled):
        func_to_call = functools.partial(_call_asm_wrap, func, instance)
    else:
        func_to_call = func

    pin = Pin.get_from(instance)
    if should_skip_request(pin, instance):
        return func_to_call(*args, **kwargs)

    cfg = Pin._get_config(instance)

    try:
        # Create a new span and attach to this instance (so we can retrieve/update/close later on the response)
        span = pin.tracer.trace(span_name, span_type=SpanTypes.HTTP)

        span.set_tag_str(COMPONENT, config.httplib.integration_name)

        # set span.kind to the type of operation being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        instance._datadog_span = span

        # propagate distributed tracing headers
        if cfg.get("distributed_tracing"):
            if len(args) > 3:
                headers = args[3]
            else:
                headers = kwargs.setdefault("headers", {})
            HTTPPropagator.inject(span.context, headers)
    except Exception:
        log.debug("error configuring request", exc_info=True)
        span = getattr(instance, "_datadog_span", None)
        if span:
            span.finish()

    try:
        return func_to_call(*args, **kwargs)
    except Exception:
        span = getattr(instance, "_datadog_span", None)
        exc_info = sys.exc_info()
        if span:
            span.set_exc_info(*exc_info)
            span.finish()
        raise


def _wrap_putrequest(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    if should_skip_request(pin, instance):
        return func(*args, **kwargs)

    try:
        if hasattr(instance, "_datadog_span"):
            # Reuse an existing span set in _wrap_request
            span = instance._datadog_span
        else:
            # Create a new span and attach to this instance (so we can retrieve/update/close later on the response)
            span = pin.tracer.trace(span_name, span_type=SpanTypes.HTTP)

            span.set_tag_str(COMPONENT, config.httplib.integration_name)

            # set span.kind to the type of operation being performed
            span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

            instance._datadog_span = span

        method, path = args[:2]
        scheme = "https" if isinstance(instance, httplib.HTTPSConnection) else "http"
        port = ":{port}".format(port=instance.port)

        if (scheme == "http" and instance.port == 80) or (scheme == "https" and instance.port == 443):
            port = ""
        url = "{scheme}://{host}{port}{path}".format(scheme=scheme, host=instance.host, port=port, path=path)

        # sanitize url
        parsed = parse.urlparse(url)
        sanitized_url = parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, None, parsed.fragment)  # drop query
        )
        trace_utils.set_http_meta(
            span, config.httplib, method=method, url=sanitized_url, target_host=instance.host, query=parsed.query
        )

    except Exception:
        log.debug("error applying request tags", exc_info=True)

        # Close the span to prevent memory leaks.
        span = getattr(instance, "_datadog_span", None)
        if span:
            span.finish()

    try:
        return func(*args, **kwargs)
    except Exception:
        span = getattr(instance, "_datadog_span", None)
        exc_info = sys.exc_info()
        if span:
            span.set_exc_info(*exc_info)
            span.finish()
        raise


def _wrap_putheader(func, instance, args, kwargs):
    span = getattr(instance, "_datadog_span", None)
    if span:
        request_headers = {args[0]: args[1]}
        trace_utils.set_http_meta(span, config.httplib, request_headers=request_headers)

    return func(*args, **kwargs)


def should_skip_request(pin, request):
    """Helper to determine if the provided request should be traced"""
    if getattr(request, _HTTPLIB_NO_TRACE_REQUEST, False):
        return True

    if not pin or not pin.enabled():
        return True

    # httplib is used to send apm events (profiling,di, tracing, etc.) to the datadog agent
    # Tracing these requests introduces a significant noise and instability in ddtrace tests.
    # TO DO: Avoid tracing requests to APM internal services (ie: extend this functionality to agentless products).
    agent_url = pin.tracer.agent_trace_url
    if agent_url:
        parsed = parse.urlparse(agent_url)
        return request.host == parsed.hostname and request.port == parsed.port
    return False


def patch():
    """patch the built-in urllib/httplib/httplib.client methods for tracing"""
    if getattr(httplib, "__datadog_patch", False):
        return
    httplib.__datadog_patch = True

    # Patch the desired methods
    httplib.HTTPConnection.__init__ = wrapt.FunctionWrapper(httplib.HTTPConnection.__init__, _wrap_init)
    httplib.HTTPConnection.getresponse = wrapt.FunctionWrapper(httplib.HTTPConnection.getresponse, _wrap_getresponse)
    httplib.HTTPConnection.request = wrapt.FunctionWrapper(httplib.HTTPConnection.request, _wrap_request)
    httplib.HTTPConnection.putrequest = wrapt.FunctionWrapper(httplib.HTTPConnection.putrequest, _wrap_putrequest)
    httplib.HTTPConnection.putheader = wrapt.FunctionWrapper(httplib.HTTPConnection.putheader, _wrap_putheader)


def unpatch():
    """unpatch any previously patched modules"""
    if not getattr(httplib, "__datadog_patch", False):
        return
    httplib.__datadog_patch = False

    _u(httplib.HTTPConnection, "__init__")
    _u(httplib.HTTPConnection, "getresponse")
    _u(httplib.HTTPConnection, "request")
    _u(httplib.HTTPConnection, "putrequest")
    _u(httplib.HTTPConnection, "putheader")
