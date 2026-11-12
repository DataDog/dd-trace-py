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
from ddtrace.contrib.internal.mcp._constants import DD_TRACE_CONTEXT_META_KEY
from ddtrace.contrib.internal.mcp._constants import SERVER_MIDDLEWARE_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_REQUEST_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import TELEMETRY_KEY
from ddtrace.contrib.internal.mcp._tool_result import DEFAULT_TOOL_ERROR_MESSAGE
from ddtrace.contrib.internal.mcp._tool_result import get_tool_error_message
from ddtrace.contrib.internal.mcp._tool_result import is_tool_error
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.internal.logger import get_logger
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


class DatadogServerMiddleware:
    """mcp>=2 server middleware. Every transport and protocol version runs each inbound
    message through Server.middleware, so this replaces the RequestResponder patches used for mcp<2.
    """

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        # Typed loosely: contrib must not import the LLM Observability integration class
        integration: Any = getattr(mcp, "_datadog_integration", None)
        # Server.middleware is provisional upstream, so ctx fields are read defensively
        method = getattr(ctx, "method", None)
        if integration is None or getattr(ctx, "request_id", None) is None:
            return await call_next(ctx)

        if method == "tools/list":
            result = await call_next(ctx)
            if config.mcp.capture_intent:
                integration.inject_tools_list_response(result)
            return result

        # While this middleware sees all requests, we only trace these types right now
        if method not in ("initialize", "tools/call"):
            return await call_next(ctx)

        try:
            span, ctx = start_server_span(integration, method, ctx)
        except Exception:
            log.debug("Error starting MCP server span", exc_info=True)
            return await call_next(ctx)

        result = None
        try:
            result = await call_next(ctx)
            if method == "tools/call" and is_tool_error(result):
                span.error = 1
                span.set_tag(ERROR_TYPE, "ToolError")
                span.set_tag(ERROR_MSG, get_tool_error_message(result) or DEFAULT_TOOL_ERROR_MESSAGE)
            return result
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            integration.llmobs_set_tags(
                span, args=[], kwargs={"ctx": ctx}, response=result, operation=SERVER_MIDDLEWARE_OPERATION_NAME
            )
            span.finish()


def start_server_span(integration: Any, method: str, ctx: Any) -> tuple[Span, Any]:
    if (
        method == "tools/call"
        and config.mcp.distributed_tracing
        and (headers := (getattr(ctx, "meta", None) or {}).get(DD_TRACE_CONTEXT_META_KEY))
    ):
        activate_distributed_headers(tracer, config.mcp, headers)

    operation_name = SERVER_TOOL_CALL_OPERATION_NAME if method == "tools/call" else SERVER_REQUEST_OPERATION_NAME
    span = integration.trace(operation_name, submit_to_llmobs=True, span_name=f"mcp.{method}")

    if method == "tools/call":
        try:
            ctx = strip_telemetry_argument(integration, span, ctx)
        except Exception:
            span.finish()
            raise
    return span, ctx


def strip_telemetry_argument(integration: Any, span: Span, ctx: Any) -> Any:
    """Records and removes the intent argument. Middleware must not mutate the wire params, so
    the stripped copy is passed down the chain through a replaced context.
    """
    params = ctx.params or {}
    arguments = params.get("arguments")
    if not isinstance(arguments, dict) or TELEMETRY_KEY not in arguments:
        return ctx
    params = {**params, "arguments": dict(arguments)}
    integration.process_telemetry_argument(span, {"params": params})
    return dataclasses.replace(ctx, params=params)
