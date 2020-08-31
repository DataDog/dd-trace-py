import functools

from ..asyncio import context_provider
from ...compat import stringify
from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...ext import SpanTypes, http
from ...propagation.http import HTTPPropagator
from ...pin import Pin
from ...settings import config
from ...context import Context
from ...utils.wrappers import iswrapped
from .patch import _WrappedStreamReader

try:
    from aiohttp.web import middleware

    AIOHTTP_HAS_MIDDLEWARE = True
except ImportError:
    AIOHTTP_HAS_MIDDLEWARE = False

    def middleware(f):
        return f

import aiohttp.streams


CONFIG_KEY = "datadog_trace"
REQUEST_CONTEXT_KEY = "datadog_context"
REQUEST_CONFIG_KEY = "__datadog_trace_config"
REQUEST_SPAN_KEY = "__datadog_request_span"

propagator = HTTPPropagator()


config._add("aiohttp_server", dict(
    service="aiohttp.server",
))


@middleware
async def trace_middleware_2x(request, handler, app=None):
    # application configs
    if app is None:
        app = request.app

    tracer = app[CONFIG_KEY]["tracer"]
    service = app[CONFIG_KEY]["service"]
    distributed_tracing = app[CONFIG_KEY]["distributed_tracing_enabled"]

    # Create a new context based on the propagated information.
    if distributed_tracing:
        context = propagator.extract(request.headers)

        if context.trace_id:
            tracer.context_provider.activate(context)
        else:
            # In case a non-distributed request comes after a distributed request we need to clear out
            # the previous context
            tracer.context_provider.activate(Context())

    # trace the handler
    request_span = tracer.trace("aiohttp.request", service=service, span_type=SpanTypes.WEB)
    # Unset resource as we'll be setting this in all cases
    request_span.resource = None

    request_span.set_tag(SPAN_MEASURED_KEY)

    # Configure trace search sample rate
    analytics_enabled = app[CONFIG_KEY]["analytics_enabled"]
    if (config.analytics_enabled and analytics_enabled is not False) or analytics_enabled is True:
        request_span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, app[CONFIG_KEY].get("analytics_sample_rate", True))

    # attach the context and the root span to the request; the Context
    # may be freely used by the application code
    request[REQUEST_CONTEXT_KEY] = request_span.context
    request[REQUEST_SPAN_KEY] = request_span
    request[REQUEST_CONFIG_KEY] = app[CONFIG_KEY]

    if request.match_info.route.resource:
        # collect the resource name based on http resource type
        res_info = request.match_info.route.resource.get_info()

        resource = None
        if res_info.get("path"):
            resource = res_info.get("path")
        elif res_info.get("formatter"):
            resource = res_info.get("formatter")
        elif res_info.get("prefix"):
            resource = res_info.get("prefix")

        if resource:
            # prefix the resource name by the http method
            resource = "{} {}".format(request.method, resource)
            request_span.resource = resource

    request_span.set_tag(http.METHOD, request.method)
    request_span.set_tag(http.URL, request.url.with_query(None))
    trace_query_string = request[REQUEST_CONFIG_KEY].get("trace_query_string")
    if trace_query_string is None:
        trace_query_string = config.http.trace_query_string
    if trace_query_string:
        request_span.set_tag(http.QUERY_STRING, request.query_string)

    if not iswrapped(request._payload) and isinstance(request._payload, aiohttp.streams.StreamReader):
        tags = {tag: request_span.get_tag(tag) for tag in {http.URL, http.METHOD, http.QUERY_STRING}
                if request_span.get_tag(tag)}

        pin = Pin(service, tracer=tracer, tags=tags, _config=config.aiohttp_server)
        request._payload = _WrappedStreamReader(request._payload, pin, request_span.trace_id, request_span.span_id,
                                                request_span.resource)

    try:
        response = await handler(request)
        return response
    except Exception:
        request_span.set_traceback()
        raise


async def trace_middleware_1x(app, handler):
    """
    ``aiohttp`` middleware that traces the handler execution.
    Because handlers are run in different tasks for each request, we attach the Context
    instance both to the Task and to the Request objects. In this way:

    * the Task is used by the internal automatic instrumentation
    * the ``Context`` attached to the request can be freely used in the application code
    """
    return functools.partial(trace_middleware_2x, handler=handler, app=app)


trace_middleware = trace_middleware_2x if AIOHTTP_HAS_MIDDLEWARE else trace_middleware_1x


async def on_prepare(request, response):
    """
    The on_prepare signal is used to close the request span that is created during
    the trace middleware execution.
    """
    # safe-guard: discard if we don't have a request span
    request_span = request.get(REQUEST_SPAN_KEY, None)
    if not request_span:
        return

    # default resource name
    if not request_span.resource:
        request_span.resource = stringify(response.status)

    request_span.set_tag(http.STATUS_CODE, response.status)

    if 500 <= response.status < 600:
        request_span.error = 1

    request_span.finish()


def trace_app(app, tracer, service="aiohttp-web", distributed_tracing=True):
    """
    Tracing function that patches the ``aiohttp`` application so that it will be
    traced using the given ``tracer``.

    :param app: aiohttp application to trace
    :param tracer: tracer instance to use
    :param service: service name of tracer
    :param distributed_tracing: set to True to enable distributed tracing
    """

    # safe-guard: don't trace an application twice
    if getattr(app, "__datadog_trace", False):
        return
    setattr(app, "__datadog_trace", True)

    # configure datadog settings
    app[CONFIG_KEY] = {
        "tracer": tracer,
        "service": config._get_service(default=service),
        "distributed_tracing_enabled": distributed_tracing,
        "analytics_enabled": None,
        "analytics_sample_rate": 1.0,
    }

    # the tracer must work with asynchronous Context propagation
    tracer.configure(context_provider=context_provider)

    # add the async tracer middleware as a first middleware
    # and be sure that the on_prepare signal is the last one
    app.middlewares.insert(0, trace_middleware)
    app.on_response_prepare.append(on_prepare)
