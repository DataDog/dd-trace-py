from aiohttp import web
from aiohttp.web_urldispatcher import SystemRoute

from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


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
        # Create a new context based on the propagated information.

        with core.context_with_data(
            "aiohttp.request",
            span_name=schematize_url_operation("aiohttp.request", protocol="http", direction=SpanDirection.INBOUND),
            span_type=SpanTypes.WEB,
            service=service,
            tags={},
            tracer=tracer,
            distributed_headers=request.headers,
            distributed_headers_config=config.aiohttp,
            distributed_headers_config_override=app[CONFIG_KEY]["distributed_tracing_enabled"],
            headers_case_sensitive=True,
        ) as ctx:
            req_span = ctx.span

            ctx.set_item("req_span", req_span)
            core.dispatch("web.request.start", (ctx, config.aiohttp))

            # attach the context and the root span to the request; the Context
            # may be freely used by the application code
            request[REQUEST_CONTEXT_KEY] = req_span.context
            request[REQUEST_SPAN_KEY] = req_span
            request[REQUEST_CONFIG_KEY] = app[CONFIG_KEY]
            try:
                response = await handler(request)
                if not config.aiohttp["disable_stream_timing_for_mem_leak"]:
                    if isinstance(response, web.StreamResponse):
                        request.task.add_done_callback(lambda _: finish_request_span(request, response))
                return response
            except Exception:
                req_span.set_traceback()
                raise

    return attach_context


def finish_request_span(request, response):
    # safe-guard: discard if we don't have a request span
    request_span = request.get(REQUEST_SPAN_KEY, None)
    if not request_span:
        return

    # default resource name
    resource = str(response.status)

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
        trace_query_string = config._http.trace_query_string
    if trace_query_string:
        request_span.set_tag_str(http.QUERY_STRING, request.query_string)

    # The match info object provided by aiohttp's default (and only) router
    # has a `route` attribute, but routers are susceptible to being replaced/hand-rolled
    # so we can only support this case.
    route = None
    if hasattr(request.match_info, "route"):
        aiohttp_route = request.match_info.route
        if not isinstance(aiohttp_route, SystemRoute):
            # SystemRoute objects exist to throw HTTP errors and have no path
            route = aiohttp_route.resource.canonical

    core.dispatch(
        "web.request.finish",
        (
            request_span,
            config.aiohttp,
            request.method,
            str(request.url),  # DEV: request.url is a yarl's URL object
            response.status,
            None,  # query arg = None
            request.headers,
            response.headers,
            route,
            True,
        ),
    )


async def on_prepare(request, response):
    """
    The on_prepare signal is used to close the request span that is created during
    the trace middleware execution.
    """
    # NB isinstance is not appropriate here because StreamResponse is a parent of the other
    # aiohttp response types. However in some cases this can also lead to missing the closing of
    # spans, leading to a memory leak, which is why we have this flag.
    # todo: this is a temporary fix for a memory leak in aiohttp. We should find a way to
    # consistently close spans with the correct timing.
    if not config.aiohttp["disable_stream_timing_for_mem_leak"]:
        if type(response) is web.StreamResponse and not response.task.done():
            return
    finish_request_span(request, response)


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
    app.__datadog_trace = True

    # configure datadog settings
    app[CONFIG_KEY] = {
        "tracer": tracer,
        "service": config._get_service(default=service),
        "distributed_tracing_enabled": None,
    }

    # add the async tracer middleware as a first middleware
    # and be sure that the on_prepare signal is the last one
    app.middlewares.insert(0, trace_middleware)
    app.on_response_prepare.append(on_prepare)
