import os
from typing import Dict
from urllib.parse import urlencode

import molten
import wrapt
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.utils.version import parse_version
from ddtrace.trace import Pin

from .wrappers import MOLTEN_ROUTE
from .wrappers import WrapperComponent
from .wrappers import WrapperMiddleware
from .wrappers import WrapperRenderer
from .wrappers import WrapperRouter


MOLTEN_VERSION = parse_version(molten.__version__)

# Configure default configuration
config._add(
    "molten",
    dict(
        _default_service=schematize_service_name("molten"),
        distributed_tracing=asbool(os.getenv("DD_MOLTEN_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(molten, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"molten": ">=1.0"}


def patch():
    """Patch the instrumented methods"""
    if getattr(molten, "_datadog_patch", False):
        return
    molten._datadog_patch = True

    pin = Pin()

    # add pin to module since many classes use __slots__
    pin.onto(molten)

    _w(molten.BaseApp, "__init__", patch_app_init)
    _w(molten.App, "__call__", patch_app_call)


def unpatch():
    """Remove instrumentation"""
    if getattr(molten, "_datadog_patch", False):
        molten._datadog_patch = False

        # remove pin
        pin = Pin.get_from(molten)
        if pin:
            pin.remove_from(molten)

        _u(molten.BaseApp, "__init__")
        _u(molten.App, "__call__")


def patch_app_call(wrapped, instance, args, kwargs):
    """Patch wsgi interface for app"""
    pin = Pin.get_from(molten)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # DEV: This is safe because this is the args for a WSGI handler
    #   https://www.python.org/dev/peps/pep-3333/
    environ, start_response = args

    request = molten.http.Request.from_environ(environ)
    resource = func_name(wrapped)

    with core.context_with_data(
        "molten.request",
        span_name=schematize_url_operation("molten.request", protocol="http", direction=SpanDirection.INBOUND),
        span_type=SpanTypes.WEB,
        service=trace_utils.int_service(pin, config.molten),
        resource=resource,
        tags={},
        tracer=pin.tracer,
        distributed_headers=dict(request.headers),  # request.headers is type Iterable[Tuple[str, str]]
        integration_config=config.molten,
        activate_distributed_headers=True,
        headers_case_sensitive=True,
    ) as ctx, ctx.span as req_span:
        ctx.set_item("req_span", req_span)
        core.dispatch("web.request.start", (ctx, config.molten))

        @wrapt.function_wrapper
        def _w_start_response(wrapped, instance, args, kwargs):
            """Patch respond handling to set metadata"""

            pin = Pin.get_from(molten)
            if not pin or not pin.enabled():
                return wrapped(*args, **kwargs)

            status, headers, exc_info = args
            code, _, _ = status.partition(" ")

            try:
                code = int(code)
            except ValueError:
                pass

            if not req_span.get_tag(MOLTEN_ROUTE):
                # if route never resolve, update root resource
                req_span.resource = "{} {}".format(request.method, code)

            core.dispatch(
                "web.request.finish", (req_span, config.molten, None, None, code, None, None, None, None, False)
            )

            return wrapped(*args, **kwargs)

        # patching for extracting response code
        start_response = _w_start_response(start_response)

        url = "%s://%s:%s%s" % (
            request.scheme,
            request.host,
            request.port,
            request.path,
        )
        query = urlencode(dict(request.params))

        core.dispatch(
            "web.request.finish",
            (req_span, config.molten, request.method, url, None, query, request.headers, None, None, False),
        )

        req_span.set_tag_str("molten.version", molten.__version__)
        return wrapped(environ, start_response, **kwargs)


def patch_app_init(wrapped, instance, args, kwargs):
    """Patch app initialization of middleware, components and renderers"""
    # allow instance to be initialized before wrapping them
    wrapped(*args, **kwargs)

    # add Pin to instance
    pin = Pin.get_from(molten)

    if not pin or not pin.enabled():
        return

    # Wrappers here allow us to trace objects without altering class or instance
    # attributes, which presents a problem when classes in molten use
    # ``__slots__``

    instance.router = WrapperRouter(instance.router)

    # wrap middleware functions/callables
    instance.middleware = [WrapperMiddleware(mw) for mw in instance.middleware]

    # wrap components objects within injector
    # NOTE: the app instance also contains a list of components but it does not
    # appear to be used for anything passing along to the dependency injector
    instance.injector.components = [WrapperComponent(c) for c in instance.injector.components]

    # but renderers objects
    instance.renderers = [WrapperRenderer(r) for r in instance.renderers]
