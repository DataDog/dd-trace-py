import asyncio
from asyncio import current_task
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import MutableMapping
from contextvars import Context
from contextvars import copy_context
import sys
from typing import Any
from typing import Optional
from typing import cast

from aiohttp import web
from aiohttp.streams import StreamReader
from aiohttp.web_urldispatcher import SystemRoute

from ddtrace import config
from ddtrace._trace.http_tags import is_otel_non_error_status
from ddtrace.contrib._events.exception import ContextExceptionEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._exceptions import find_exception
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from ddtrace.internal.span_bus import find_span_on_context
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.http import _format_template


CONFIG_KEY = "datadog_trace"
REQUEST_CONTEXT_KEY = "datadog_context"
REQUEST_EXECUTION_CONTEXT_KEY = "__datadog_execution_context"
REQUEST_EXECUTION_CONTEXTS_KEY = "__datadog_execution_contexts"
REQUEST_CONFIG_KEY = "__datadog_trace_config"
REQUEST_STARTED_RESPONSE_KEY = "__datadog_started_response"
DEFAULT_CLIENT_MAX_SIZE = 1024**2


class _RequestLifecycle:
    def __init__(
        self,
        request: web.Request,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        task: asyncio.Future[Any],
        registry: dict[asyncio.Future[Any], list["_RequestLifecycle"]],
    ) -> None:
        self.request = request
        self.ctx = ctx
        self._task: Optional[asyncio.Future[Any]] = task
        self._connection_task: Optional[asyncio.Future[Any]] = request.task
        self._registry = registry
        self._connection_key: Optional[asyncio.Future[Any]] = self._connection_task
        if self._connection_key is not None:
            registry.setdefault(self._connection_key, []).append(self)
        self._preparation_tasks: set[asyncio.Future[Any]] = set()
        # Preparation can run outside the task that created the span and IAST context.
        self._completion_context: Optional[Context] = copy_context()
        self._task_completed = False
        self._response_prepared = False
        task.add_done_callback(self._on_task_done)
        if self._connection_task is not None:
            self._connection_task.add_done_callback(self._on_connection_done)

    def _on_task_done(self, task: asyncio.Future[Any]) -> None:
        # A completed task can retain its exception and handler frames.
        self._task = None
        self._task_completed = True
        if self._connection_task is None:
            self._finish()
        else:
            self._maybe_finish()

    def _on_preparation_done(self, task: asyncio.Future[Any]) -> None:
        self._preparation_tasks.discard(task)
        self._maybe_finish()

    def _on_connection_done(self, task: asyncio.Future[Any]) -> None:
        self._connection_task = None
        # An aborted request may never reach Datadog's preparation callback.
        self._finish()

    def on_prepare(self, response: web.StreamResponse, finish_on_prepare: bool) -> None:
        self._response_prepared = True
        self.ctx.set_item("aiohttp.response", response)
        if finish_on_prepare:
            self._finish(response)
            return
        preparation_task = current_task()
        if (
            preparation_task is not None
            and preparation_task is not self._task
            and preparation_task not in self._preparation_tasks
        ):
            self._preparation_tasks.add(preparation_task)
            preparation_task.add_done_callback(self._on_preparation_done)
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if self._task_completed and self._response_prepared and not self._preparation_tasks:
            self._finish()

    def _finish(self, response: Optional[web.StreamResponse] = None) -> None:
        completion_context = self._completion_context
        if completion_context is None:
            return
        self._completion_context = None
        if self._task is not None and current_task() is self._task:
            # Early teardown must clear ContextVars in the still-running handler itself.
            finish_request_span(self.request, response, ctx=self.ctx)
        else:
            completion_context.run(finish_request_span, self.request, response, ctx=self.ctx)

    def close(self) -> None:
        if self._connection_key is not None:
            lifecycles = self._registry.get(self._connection_key)
            if lifecycles is not None and self in lifecycles:
                lifecycles.remove(self)
                if not lifecycles:
                    self._registry.pop(self._connection_key, None)
            self._connection_key = None
        for task in self._preparation_tasks:
            task.remove_done_callback(self._on_preparation_done)
        self._preparation_tasks.clear()
        if self._task is not None:
            self._task.remove_done_callback(self._on_task_done)
            self._task = None
        if self._connection_task is not None:
            self._connection_task.remove_done_callback(self._on_connection_done)
            self._connection_task = None
        self._completion_context = None
        self.ctx.discard_local_item("aiohttp.lifecycle")


async def trace_middleware(
    app: web.Application, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
    """
    ``aiohttp`` middleware that traces the handler execution.
    Because handlers are run in different tasks for each request, we attach the Context
    instance both to the Task and to the Request objects. In this way:

    * the Task is used by the internal automatic instrumentation
    * the ``Context`` attached to the request can be freely used in the application code
    """

    async def attach_context(request: web.Request) -> web.StreamResponse:
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
            if not isinstance(aiohttp_route, SystemRoute) and aiohttp_route.resource is not None:
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
                request_headers=cast(MutableMapping[str, str], request.headers),
                request_method=request.method,
                request_url=str(request.url),  # DEV: request.url is a yarl's URL object,
                request_route=route,
                headers_case_sensitive=True,
                activate_distributed_headers=True,
                distributed_headers_config_override=app_config["distributed_tracing_enabled"],
                trace_query_string=trace_query_string,
                query=request.rel_url.raw_query_string,
                service=service,
            ),
            dispatch_end_event=False,
        ) as ctx:
            req_span = find_span_on_context(ctx)

            # Clones copy request state shallowly, so response preparation can find every middleware owner.
            request.setdefault(REQUEST_EXECUTION_CONTEXTS_KEY, []).append(ctx)
            request[REQUEST_EXECUTION_CONTEXT_KEY] = ctx
            # legacy request key kept for backwards compatibility with documentation
            request[REQUEST_CONTEXT_KEY] = req_span.context if req_span is not None else None

            # The handler task can finish before an outer middleware prepares the response.
            task = current_task()
            finish_on_prepare = config.aiohttp["disable_stream_timing_for_mem_leak"] or task is None
            ctx.set_item("aiohttp.finish_on_prepare", finish_on_prepare)
            if task is not None:
                ctx.set_item(
                    "aiohttp.lifecycle", _RequestLifecycle(request, ctx, task, app_config["_request_lifecycles"])
                )

            try:
                try:
                    response = await _handle_request(ctx, request, handler)
                except BaseException as exc:
                    if (blocking := find_exception(exc, BlockingException)) is None:
                        raise
                    response = _blocked_response(ctx, request, blocking.args[0])
                finally:
                    core.dispatch("aiohttp.request.end", (ctx, (None, None, None)))
                # An earlier preparation callback may fail before on_prepare records the response.
                if not ctx._end_event_dispatched and ctx.get_item("aiohttp.response") is None:
                    ctx.set_item("aiohttp.response", response)
                return response
            except Exception as e:
                # With OTel semantics an HTTP exception such as HTTPNotFound only fails the span
                # when its status is an error status.
                if not (isinstance(e, web.HTTPException) and is_otel_non_error_status(e.status)):
                    core.dispatch_event(ContextExceptionEvent(context=ctx, exc_info=sys.exc_info()))
                raise
        raise RuntimeError("Request context suppressed an exception without a response")

    return attach_context


async def _handle_request(
    ctx: core.ExecutionContext[WebFrameworkRequestEvent],
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    max_body_size = getattr(request, "_client_max_size", 0) or DEFAULT_CLIENT_MAX_SIZE
    core.dispatch("aiohttp.request.start", (ctx, request, max_body_size))
    read_body = ctx.get_item("aiohttp_request_body")
    ctx.discard_local_item("aiohttp_request_body")
    if read_body is not None:
        body = await read_body
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


def _restore_request_body(request: web.Request, body: bytes) -> None:
    stream = StreamReader(request.protocol, max(len(body), 2**16), loop=asyncio.get_running_loop())
    stream.feed_data(body)
    stream.feed_eof()
    request._payload = stream
    request._cache.pop("content", None)


def _blocked_response(
    ctx: core.ExecutionContext[WebFrameworkRequestEvent], request: web.Request, block_config: Any
) -> web.StreamResponse:
    started_response = request.get(REQUEST_STARTED_RESPONSE_KEY)
    if started_response is not None:
        if request.transport is not None:
            request.transport.close()
        return cast(web.StreamResponse, started_response)
    core.dispatch("aiohttp.request.block", (ctx, block_config))
    block_response = ctx.get_item("aiohttp_block_response")
    ctx.discard_local_item("aiohttp_block_response")
    if block_response is not None:
        status, headers, body = block_response
        return web.Response(status=status, headers=headers, body=body)
    return web.Response(
        status=403, content_type="application/json", text=_format_template(BLOCKED_RESPONSE_JSON, "default")
    )


def finish_request_span(
    request: web.Request,
    response: Optional[web.StreamResponse] = None,
    *,
    ctx: Optional[core.ExecutionContext[WebFrameworkRequestEvent]] = None,
) -> None:
    # Preparation, handler completion, and connection abort can race to finish a request.
    if ctx is None:
        ctx = request.get(REQUEST_EXECUTION_CONTEXT_KEY)
    if not ctx:
        return
    lifecycle: Optional[_RequestLifecycle] = ctx.get_item("aiohttp.lifecycle")
    if lifecycle is not None:
        lifecycle.close()
    if ctx._end_event_dispatched:
        return

    if response is None:
        response = ctx.get_item("aiohttp.response", request.get(REQUEST_STARTED_RESPONSE_KEY))
    ctx.discard_local_item("aiohttp.response")
    if response is None:
        # A disconnected or cancelled request can end without preparing a response.
        ctx.dispatch_ended_event()
        return

    # default resource name
    resource = str(response.status)

    if request.match_info.route.resource:
        # collect the resource name based on http resource type
        res_info = request.match_info.route.resource.get_info()

        resource = res_info.get("path") or res_info.get("formatter") or res_info.get("prefix") or resource

        # prefix the resource name by the http method
        resource = f"{request.method} {resource}"

    event: WebFrameworkRequestEvent = ctx.event
    event.resource = resource
    event.response_status_code = response.status
    event.response_headers = response.headers
    ctx.dispatch_ended_event()


async def on_prepare(request: web.Request, response: web.StreamResponse) -> None:
    """Attach response metadata; protocol upgrades complete at the handshake."""
    request[REQUEST_STARTED_RESPONSE_KEY] = response
    # The compatibility option releases request state at header preparation.
    # todo: replace task callbacks with a response-completion signal when available.
    contexts = request.get(REQUEST_EXECUTION_CONTEXTS_KEY)
    if contexts is None:
        # An outer middleware can clone the request before tracing adds shared request state.
        contexts = [
            lifecycle.ctx
            for app in request.match_info.apps
            for lifecycle in app.get(CONFIG_KEY, {}).get("_request_lifecycles", {}).get(request.task, ())
            if lifecycle.request.match_info is request.match_info
        ]
    for ctx in reversed(contexts):
        if not ctx._end_event_dispatched:
            # Switching protocols ends the HTTP request before the upgraded connection closes.
            finish_on_prepare = response.status == 101 or ctx.get_item("aiohttp.finish_on_prepare", True)
            lifecycle: Optional[_RequestLifecycle] = ctx.get_item("aiohttp.lifecycle")
            if lifecycle is not None:
                lifecycle.on_prepare(response, finish_on_prepare)
            elif finish_on_prepare:
                finish_request_span(request, response, ctx=ctx)
            else:
                # A clone has its own request state, so retain metadata on its owning context.
                ctx.set_item("aiohttp.response", response)


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
        "_request_lifecycles": {},
    }

    # add the async tracer middleware as a first middleware
    # and be sure that the on_prepare signal is the last one
    app.middlewares.insert(0, trace_middleware)
    app.on_response_prepare.append(on_prepare)
