from ddtrace.vendor import wrapt
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

import molten

from ... import Pin, config
from ...compat import urlencode
from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...propagation.http import HTTPPropagator
from ...utils.formats import asbool, get_env
from ...utils.importlib import func_name
from ...utils.wrappers import unwrap as _u
from .. import trace_utils
from .wrappers import WrapperComponent, WrapperRenderer, WrapperMiddleware, WrapperRouter, MOLTEN_ROUTE


MOLTEN_VERSION = tuple(map(int, molten.__version__.split()[0].split(".")))

# Configure default configuration
config._add(
    "molten",
    dict(
        _default_service="molten",
        app="molten",
        distributed_tracing=asbool(get_env("molten", "distributed_tracing", default=True)),
    ),
)


def patch():
    """Patch the instrumented methods"""
    if getattr(molten, "_datadog_patch", False):
        return
    setattr(molten, "_datadog_patch", True)

    pin = Pin(app=config.molten["app"])

    # add pin to module since many classes use __slots__
    pin.onto(molten)

    _w(molten.BaseApp, "__init__", patch_app_init)
    _w(molten.App, "__call__", patch_app_call)


def unpatch():
    """Remove instrumentation"""
    if getattr(molten, "_datadog_patch", False):
        setattr(molten, "_datadog_patch", False)

        # remove pin
        pin = Pin.get_from(molten)
        if pin:
            pin.remove_from(molten)

        _u(molten.BaseApp, "__init__")
        _u(molten.App, "__call__")
        _u(molten.Router, "add_route")


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

    # Configure distributed tracing
    if config.molten.get("distributed_tracing", True):
        propagator = HTTPPropagator()
        # request.headers is type Iterable[Tuple[str, str]]
        context = propagator.extract(dict(request.headers))
        # Only need to activate the new context if something was propagated
        if context.trace_id:
            pin.tracer.context_provider.activate(context)

    with pin.tracer.trace(
        "molten.request",
        service=trace_utils.int_service(pin, config.molten),
        resource=resource,
        span_type=SpanTypes.WEB,
    ) as span:
        span.set_tag(SPAN_MEASURED_KEY)
        # set analytics sample rate with global config enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.molten.get_analytics_sample_rate(use_global_config=True))

        @wrapt.function_wrapper
        def _w_start_response(wrapped, instance, args, kwargs):
            """ Patch respond handling to set metadata """

            pin = Pin.get_from(molten)
            if not pin or not pin.enabled():
                return wrapped(*args, **kwargs)

            status, headers, exc_info = args
            code, _, _ = status.partition(" ")

            try:
                code = int(code)
            except ValueError:
                pass

            if not span.get_tag(MOLTEN_ROUTE):
                # if route never resolve, update root resource
                span.resource = u"{} {}".format(request.method, code)

            trace_utils.set_http_meta(span, config.molten, status_code=code)

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
        trace_utils.set_http_meta(
            span, config.molten, method=request.method, url=url, query=query, request_headers=request.headers
        )

        span.set_tag("molten.version", molten.__version__)
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
