from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
import dataclasses
import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

import mcp


if TYPE_CHECKING:
    from mcp.types import ClientRequest
    from mcp.types import Request


from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.mcp._constants import COMPONENT
from ddtrace.contrib.internal.mcp._constants import DD_TRACE_CONTEXT_META_KEY
from ddtrace.contrib.internal.mcp._constants import SERVER_MIDDLEWARE_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_REQUEST_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import TELEMETRY_ARGUMENT_EVENT
from ddtrace.contrib.internal.mcp._constants import TELEMETRY_KEY
from ddtrace.contrib.internal.mcp._constants import TOOLS_LIST_RESPONSE_EVENT
from ddtrace.contrib.internal.mcp._tool_result import DEFAULT_TOOL_ERROR_MESSAGE
from ddtrace.contrib.internal.mcp._tool_result import get_tool_error_message
from ddtrace.contrib.internal.mcp._tool_result import is_tool_error
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


log = get_logger(__name__)


def set_distributed_headers_into_mcp_request(request: ClientRequest) -> ClientRequest:
    """Inject distributed tracing headers into MCP request metadata."""
    span = tracer.current_span()
    if span is None:
        return request

    headers: dict[str, str] = {}
    HTTPPropagator.inject(span.context, headers)
    if not headers:
        return request
    if _get_attr(request, "root", None) is None:
        return set_distributed_headers_into_mcp_v2_request(request, headers)

    try:
        request_params = _get_attr(request.root, "params", None)
        if not request_params:
            return request

        # Use the `_meta` field to store tracing headers. It is accessed via a public
        # `meta` attribute on the request params. This field is reserved for server/clients
        # to attach additional metadata to a request. For more information, see:
        # https://modelcontextprotocol.io/specification/2025-06-18/basic#meta
        existing_meta = _get_attr(request_params, "meta", None)
        meta_dict = existing_meta.model_dump() if existing_meta else {}

        meta_dict[DD_TRACE_CONTEXT_META_KEY] = headers
        params_dict = request_params.model_dump(by_alias=True)
        params_dict["_meta"] = meta_dict

        new_params = type(request_params)(**params_dict)
        request_dict = request.root.model_dump()
        request_dict["params"] = new_params

        new_request_root = type(request.root)(**request_dict)
        return type(request)(new_request_root)
    except Exception:
        log.error("Error injecting distributed tracing headers into MCP request metadata", exc_info=True)
        return request


def set_distributed_headers_into_mcp_v2_request(request: Any, headers: dict[str, str]) -> Any:
    """mcp>=2 sends concrete request models (no RootModel wrapper) whose params._meta is a plain dict."""
    try:
        request_params = _get_attr(request, "params", None)
        if not request_params:
            return request
        meta = dict(_get_attr(request_params, "meta", None) or {})
        meta[DD_TRACE_CONTEXT_META_KEY] = headers
        return request.model_copy(update={"params": request_params.model_copy(update={"meta": meta})})
    except Exception:
        log.error("Error injecting distributed tracing headers into MCP request metadata", exc_info=True)
        return request


def extract_distributed_headers_from_mcp_request(request_root: Request) -> Optional[dict[str, str]]:
    """Extract distributed tracing headers from MCP request params.meta field."""
    request_params = _get_attr(request_root, "params", None)
    meta = _get_attr(request_params, "meta", None) if request_params else None
    meta_dict = meta.model_dump() if meta and hasattr(meta, "model_dump") else {}
    headers = meta_dict.get(DD_TRACE_CONTEXT_META_KEY, {})
    return headers if headers else None


def request_event(
    resource: str,
    operation: str,
    span_name: Optional[str] = None,
    args: tuple[Any, ...] = (),
    kwargs: Optional[dict[str, Any]] = None,
) -> LlmRequestEvent:
    event = LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.mcp,
        service=int_service(None, config.mcp),
        resource=resource,
        provider="",
        submit_to_llmobs=True,
        request_args=args,
        request_kwargs=kwargs if kwargs is not None else {},
        operation=operation,
    )
    if span_name:
        event.operation_name = span_name
    return event


def start_request(event: LlmRequestEvent) -> core.ExecutionContext[LlmRequestEvent]:
    """Start the request's span, which stays active until finish_request is called."""
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        pass
    return ctx


def finish_request(ctx: core.ExecutionContext[LlmRequestEvent], response: Any = None, error: bool = False) -> None:
    """Dispatch the request's deferred ended event, which tags and finishes the span.

    LLMObs tagging runs before the span records an error passed to the ended event, so errors are recorded first.
    """
    if error:
        span_from_context(ctx).set_exc_info(*sys.exc_info())
    ctx.event.response = response
    ctx.dispatch_ended_event()


class DatadogServerMiddleware:
    """mcp>=2 server middleware. Every transport and protocol version runs each inbound
    message through Server.middleware, so this replaces the RequestResponder patches used for mcp<2.
    """

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        # Server.middleware is provisional upstream, so ctx fields are read defensively
        method = getattr(ctx, "method", None)
        if not getattr(mcp, "__datadog_patch", False) or getattr(ctx, "request_id", None) is None:
            return await call_next(ctx)

        if method == "tools/list":
            result = await call_next(ctx)
            if config.mcp.capture_intent:
                core.dispatch(TOOLS_LIST_RESPONSE_EVENT, (result,))
            return result

        # While this middleware sees all requests, we only trace these types right now
        if method not in ("initialize", "tools/call"):
            return await call_next(ctx)

        try:
            request_ctx, ctx = start_server_request(method, ctx)
        except Exception:
            log.debug("Error starting MCP server span", exc_info=True)
            return await call_next(ctx)

        result = None
        error = False
        try:
            result = await call_next(ctx)
            if method == "tools/call" and is_tool_error(result):
                span = span_from_context(request_ctx)
                span.error = 1
                span.set_tag(ERROR_TYPE, "ToolError")
                span.set_tag(ERROR_MSG, get_tool_error_message(result) or DEFAULT_TOOL_ERROR_MESSAGE)
            return result
        except Exception:
            error = True
            raise
        finally:
            request_ctx.event.request_kwargs["ctx"] = ctx
            finish_request(request_ctx, result, error)


def start_server_request(method: str, ctx: Any) -> tuple[core.ExecutionContext[LlmRequestEvent], Any]:
    if (
        method == "tools/call"
        and config.mcp.distributed_tracing
        and (headers := (getattr(ctx, "meta", None) or {}).get(DD_TRACE_CONTEXT_META_KEY))
    ):
        activate_distributed_headers(tracer, config.mcp, headers)

    operation_name = SERVER_TOOL_CALL_OPERATION_NAME if method == "tools/call" else SERVER_REQUEST_OPERATION_NAME
    request_ctx = start_request(
        request_event(operation_name, SERVER_MIDDLEWARE_OPERATION_NAME, span_name=f"mcp.{method}")
    )

    if method == "tools/call":
        try:
            ctx = strip_telemetry_argument(span_from_context(request_ctx), ctx)
        except Exception:
            finish_request(request_ctx)
            raise
    return request_ctx, ctx


def strip_telemetry_argument(span: Span, ctx: Any) -> Any:
    """Records and removes the intent argument. Middleware must not mutate the wire params, so
    the stripped copy is passed down the chain through a replaced context.
    """
    params = ctx.params or {}
    arguments = params.get("arguments")
    if not isinstance(arguments, dict) or TELEMETRY_KEY not in arguments:
        return ctx
    params = {**params, "arguments": dict(arguments)}
    core.dispatch(TELEMETRY_ARGUMENT_EVENT, (span, {"params": params}))
    return dataclasses.replace(ctx, params=params)
