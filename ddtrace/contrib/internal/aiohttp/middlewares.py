import asyncio

from aiohttp import web
from aiohttp.streams import StreamReader
from aiohttp.web_urldispatcher import SystemRoute

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._exceptions import find_exception
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.http import _format_template


CONFIG_KEY = "datadog_trace"
REQUEST_CONTEXT_KEY = "datadog_context"
REQUEST_EXECUTION_CONTEXT_KEY = "__datadog_execution_context"
REQUEST_CONFIG_KEY = "__datadog_trace_config"
REQUEST_STARTED_RESPONSE_KEY = "__datadog_started_response"
DEFAULT_CLIENT_MAX_SIZE = 1024**2


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
                query=request.rel_url.raw_query_string,
                service=service,
            ),
            dispatch_end_event=False,
        ) as ctx:
            req_span = span_from_context(ctx)

            # attach the execution context to the request
            request[REQUEST_EXECUTION_CONTEXT_KEY] = ctx
            # legacy request key kept for backwards compatibility with documentation
            request[REQUEST_CONTEXT_KEY] = req_span.context

            try:
                try:
                    response = await _handle_request(ctx, request, handler)
                except BaseException as exc:
                    if (blocking := find_exception(exc, BlockingException)) is None:
                        raise
                    response = _blocked_response(request, blocking.args[0])
                finally:
                    core.dispatch("aiohttp.request.end", (ctx, (None, None, None)))
                if not config.aiohttp["disable_stream_timing_for_mem_leak"]:
                    if isinstance(response, web.StreamResponse):
                        request.task.add_done_callback(lambda _: finish_request_span(request, response))
                return response
            except Exception:
                req_span.set_traceback()
                raise

    return attach_context


async def _handle_request(ctx, request, handler):
    max_body_size = getattr(request, "_client_max_size", 0) or DEFAULT_CLIENT_MAX_SIZE
    request_body = core.dispatch_with_results(
        "aiohttp.request.start", (ctx, request, max_body_size)
    ).aiohttp_request_body
    if request_body and request_body.value is not None:
        body = await request_body.value
        if body is not None:
            _restore_request_body(request, body)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        if REQUEST_STARTED_RESPONSE_KEY not in request:
            core.dispatch("aiohttp.request.response", (request, exc))
        raise
    except Exception as exc:
        if REQUEST_STARTED_RESPONSE_KEY not in request and core.has_listeners("aiohttp.request.response"):
            error = web.HTTPGatewayTimeout() if isinstance(exc, asyncio.TimeoutError) else web.HTTPInternalServerError()
            core.dispatch("aiohttp.request.response", (request, error))
        raise
    if REQUEST_STARTED_RESPONSE_KEY not in request:
        core.dispatch("aiohttp.request.response", (request, response))
    return response


def _restore_request_body(request, body):
    stream = StreamReader(request.protocol, max(len(body), 2**16), loop=asyncio.get_running_loop())
    stream.feed_data(body)
    stream.feed_eof()
    request._payload = stream


def _blocked_response(request, block_config):
    started_response = request.get(REQUEST_STARTED_RESPONSE_KEY)
    if started_response is not None:
        if request.transport is not None:
            request.transport.close()
        return started_response
    block_response = core.dispatch_with_results("aiohttp.request.block", (request, block_config)).aiohttp_block_response
    if block_response and block_response.value is not None:
        status, headers, body = block_response.value
        return web.Response(status=status, headers=headers, body=body)
    return web.Response(
        status=403, content_type="application/json", text=_format_template(BLOCKED_RESPONSE_JSON, "default")
    )


def finish_request_span(request, response):
    # Response preparation and the task callback can both finalize a request; the first releases its event.
    ctx = request.get(REQUEST_EXECUTION_CONTEXT_KEY)
    if not ctx or ctx._end_event_dispatched or not span_from_context(ctx):
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
        resource = f"{request.method} {resource}"

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
    request[REQUEST_STARTED_RESPONSE_KEY] = response
    # NB isinstance is not appropriate here because StreamResponse is a parent of the other
    # aiohttp response types. However in some cases this can also lead to missing the closing of
    # spans, leading to a memory leak, which is why we have this flag.
    # todo: this is a temporary fix for a memory leak in aiohttp. We should find a way to
    # consistently close spans with the correct timing.
    if not config.aiohttp["disable_stream_timing_for_mem_leak"]:
        if type(response) is web.StreamResponse and not response.task.done():
            return
    finish_request_span(request, response)


def trace_app(app, service="aiohttp-web"):
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

    # configure datadog settings
    app[CONFIG_KEY] = {
        "service": config._get_service(default=service),
        "distributed_tracing_enabled": None,
    }

    # add the async tracer middleware as a first middleware
    # and be sure that the on_prepare signal is the last one
    app.middlewares.insert(0, trace_middleware)
    app.on_response_prepare.append(on_prepare)
