from aiohttp import web
from aiohttp.web_urldispatcher import SystemRoute

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


CONFIG_KEY = "datadog_trace"
REQUEST_CONTEXT_KEY = "datadog_context"
REQUEST_EXECUTION_CONTEXT_KEY = "__datadog_execution_context"
REQUEST_CONFIG_KEY = "__datadog_trace_config"


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
        app_config = app[CONFIG_KEY]
        service = app_config["service"]
        request[REQUEST_CONFIG_KEY] = app_config

        # The match info object provided by aiohttp's default (and only) router
        # has a `route` attribute, but routers are susceptible to being replaced/hand-rolled
        # so we can only support this case.
        route = None
        if hasattr(request.match_info, "route"):
            aiohttp_route = request.match_info.route
            if not isinstance(aiohttp_route, SystemRoute):
                # SystemRoute objects exist to throw HTTP errors and have no path
                route = aiohttp_route.resource.canonical

        trace_query_string = app_config.get("trace_query_string")
        if trace_query_string is None:
            trace_query_string = config._http.trace_query_string

        # Create a new context based on the propagated information.
        with core.context_with_event(
            WebFrameworkRequestEvent(
                http_operation="aiohttp.request",
                component=config.aiohttp.integration_name,
                integration_config=config.aiohttp,
                request_headers=request.headers,
                request_method=request.method,
                request_url=str(request.url),  # DEV: request.url is a yarl's URL object,
                request_route=route,
                headers_case_sensitive=True,
                activate_distributed_headers=True,
                distributed_headers_config_override=app_config["distributed_tracing_enabled"],
                # DEV: aiohttp is special case maintains separate configuration from config api
                trace_query_string=trace_query_string,
                query=request.query_string,
                service=service,
            ),
            dispatch_end_event=False,
        ) as ctx:
            req_span = ctx.span

            # attach the execution context to the request
            request[REQUEST_EXECUTION_CONTEXT_KEY] = ctx
            # legacy request key kept for backwards compatibility with documentation
            request[REQUEST_CONTEXT_KEY] = req_span.context

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
    ctx = request.get(REQUEST_EXECUTION_CONTEXT_KEY)
    if not ctx or not ctx.span:
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

    event: WebFrameworkRequestEvent = ctx.event
    event.resource = resource
    event.response_status_code = response.status
    event.response_headers = response.headers
    ctx.dispatch_ended_event()


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


def trace_app(app, tracer=None, service="aiohttp-web"):
    """
    Tracing function that patches the ``aiohttp`` application so that it will be
    traced using the global tracer.

    :param app: aiohttp application to trace
    :param service: service name of tracer
    """

    # safe-guard: don't trace an application twice
    if getattr(app, "__datadog_trace", False):
        return
    app.__datadog_trace = True

    if tracer is not None:
        deprecate(
            "The tracer parameter is deprecated",
            message="The global tracer will be used instead.",
            category=DDTraceDeprecationWarning,
            removal_version="5.0.0",
        )
    # configure datadog settings
    app[CONFIG_KEY] = {
        "service": config._get_service(default=service),
        "distributed_tracing_enabled": None,
    }

    # add the async tracer middleware as a first middleware
    # and be sure that the on_prepare signal is the last one
    app.middlewares.insert(0, trace_middleware)
    app.on_response_prepare.append(on_prepare)
