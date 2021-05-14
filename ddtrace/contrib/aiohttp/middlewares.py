from ddtrace import config

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import http
from ...internal.compat import stringify
from ..asyncio import context_provider


CONFIG_KEY = "datadog_trace"
REQUEST_CONTEXT_KEY = "datadog_context"
REQUEST_CONFIG_KEY = "__datadog_trace_config"
REQUEST_SPAN_KEY = "__datadog_request_span"


async def trace_middleware(app, handler):
    """
    ``aiohttp`` middleware that traces the handler execution.
    Because handlers are run in different tasks for each request, we attach the Context
    instance both to the Task and to the Request objects. In this way:

    * the Task is used by the internal automatic instrumentation
    * the ``Context`` attached to the request can be freely used in the application code
    """

    async def attach_context(request):
        # application configs
        tracer = app[CONFIG_KEY]["tracer"]
        service = app[CONFIG_KEY]["service"]
        distributed_tracing = app[CONFIG_KEY]["distributed_tracing_enabled"]
        # Create a new context based on the propagated information.
        trace_utils.activate_distributed_headers(
            tracer,
            int_config=config.aiohttp,
            request_headers=request.headers,
            override=distributed_tracing,
        )

        # trace the handler
        request_span = tracer.trace(
            "aiohttp.request",
            service=service,
            span_type=SpanTypes.WEB,
        )
        request_span.set_tag(SPAN_MEASURED_KEY)

        # Configure trace search sample rate
        # DEV: aiohttp is special case maintains separate configuration from config api
        analytics_enabled = app[CONFIG_KEY]["analytics_enabled"]
        if (config.analytics_enabled and analytics_enabled is not False) or analytics_enabled is True:
            request_span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, app[CONFIG_KEY].get("analytics_sample_rate", True))

        # attach the context and the root span to the request; the Context
        # may be freely used by the application code
        request[REQUEST_CONTEXT_KEY] = request_span.context
        request[REQUEST_SPAN_KEY] = request_span
        request[REQUEST_CONFIG_KEY] = app[CONFIG_KEY]
        try:
            response = await handler(request)
            return response
        except Exception:
            request_span.set_traceback()
            raise

    return attach_context


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
    resource = stringify(response.status)

    if request.match_info.route.resource:
        # collect the resource name based on http resource type
        res_info = request.match_info.route.resource.get_info()

        if res_info.get("path"):
            resource = res_info.get("path")
        elif res_info.get("formatter"):
            resource = res_info.get("formatter")
        elif res_info.get("prefix"):
            resource = res_info.get("prefix")

        # prefix the resource name by the http method
        resource = "{} {}".format(request.method, resource)

    request_span.resource = resource

    # DEV: aiohttp is special case maintains separate configuration from config api
    trace_query_string = request[REQUEST_CONFIG_KEY].get("trace_query_string")
    if trace_query_string is None:
        trace_query_string = config.http.trace_query_string
    if trace_query_string:
        request_span.set_tag(http.QUERY_STRING, request.query_string)

    trace_utils.set_http_meta(
        request_span,
        config.aiohttp,
        method=request.method,
        url=str(request.url),  # DEV: request.url is a yarl's URL object
        status_code=response.status,
        request_headers=request.headers,
        response_headers=response.headers,
    )

    request_span.finish()


def trace_app(app, tracer, service="aiohttp-web"):
    """
    Tracing function that patches the ``aiohttp`` application so that it will be
    traced using the given ``tracer``.

    :param app: aiohttp application to trace
    :param tracer: tracer instance to use
    :param service: service name of tracer
    """

    # safe-guard: don't trace an application twice
    if getattr(app, "__datadog_trace", False):
        return
    setattr(app, "__datadog_trace", True)

    # configure datadog settings
    app[CONFIG_KEY] = {
        "tracer": tracer,
        "service": config._get_service(default=service),
        "distributed_tracing_enabled": None,
        "analytics_enabled": None,
        "analytics_sample_rate": 1.0,
    }

    # the tracer must work with asynchronous Context propagation
    tracer.configure(context_provider=context_provider)

    # add the async tracer middleware as a first middleware
    # and be sure that the on_prepare signal is the last one
    app.middlewares.insert(0, trace_middleware)
    app.on_response_prepare.append(on_prepare)
