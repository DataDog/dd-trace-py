"""Tracing shared by the mcp 1.x (_patch_v1) and mcp 2.x (_patch_v2) instrumentation."""

from __future__ import annotations

import sys
from typing import Any
from typing import Callable
from typing import Optional

import mcp

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.mcp import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_REQUEST_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.llmobs._integrations.mcp import is_tool_error_result
from ddtrace.llmobs._utils import _get_attr
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


log = get_logger(__name__)

DD_TRACE_CONTEXT_KEY = "_dd_trace_context"

# While the server patches can trace all requests, we only trace these methods right now
TRACED_SERVER_METHODS = ("initialize", "tools/call")


def _integration() -> MCPIntegration:
    integration: MCPIntegration = mcp._datadog_integration
    return integration


def inject_distributed_headers(request: Any) -> Any:
    """Return a copy of an MCP request model with the current trace context in its params metadata.

    Returns the request unchanged when there is no active span or the request has no params.
    """
    span = tracer.current_span()
    if span is None:
        return request

    headers: dict[str, str] = {}
    HTTPPropagator.inject(span.context, headers)
    if not headers:
        return request

    try:
        request_params = _get_attr(request, "params", None)
        if not request_params:
            return request

        # Use the `_meta` field to store tracing headers. It is accessed via a public
        # `meta` attribute on the request params. This field is reserved for server/clients
        # to attach additional metadata to a request. For more information, see:
        # https://modelcontextprotocol.io/specification/2025-06-18/basic#meta
        existing_meta = _get_attr(request_params, "meta", None)
        # mcp 1 models the metadata as a pydantic model, mcp 2 as a TypedDict.
        if hasattr(existing_meta, "model_dump"):
            meta_dict = existing_meta.model_dump()
        else:
            meta_dict = dict(existing_meta) if existing_meta else {}

        meta_dict[DD_TRACE_CONTEXT_KEY] = headers
        params_dict = request_params.model_dump(by_alias=True)
        params_dict["_meta"] = meta_dict

        request_dict = request.model_dump()
        request_dict["params"] = type(request_params)(**params_dict)
        return type(request)(**request_dict)
    except Exception:
        log.error("Error injecting distributed tracing headers into MCP request metadata", exc_info=True)
        return request


def activate_distributed_context(meta: Optional[dict[str, Any]]) -> None:
    """Continue the client's trace from the headers injected into a tool call's params metadata."""
    if not config.mcp.distributed_tracing or not isinstance(meta, dict):
        return
    headers = meta.get(DD_TRACE_CONTEXT_KEY)
    if headers and isinstance(headers, dict):
        activate_distributed_headers(tracer, config.mcp, headers)


def start_server_request_span(method: str, request: Any) -> Span:
    """Start the span for a traced server request; request is the parsed (mcp 1) or wire (mcp 2) request."""
    integration = _integration()
    is_tool_call = method == "tools/call"
    span = integration.trace(
        SERVER_TOOL_CALL_OPERATION_NAME if is_tool_call else SERVER_REQUEST_OPERATION_NAME,
        submit_to_llmobs=True,
        span_name=f"mcp.{method}",
    )
    if is_tool_call:
        integration.process_telemetry_argument(span, request)
    return span


def set_server_request_tags(span: Span, request: Any, response: Any, message_metadata: Any) -> None:
    _integration().llmobs_set_tags(
        span,
        args=[],
        kwargs=dict(request=request, message_metadata=message_metadata),
        response=response,
        operation=SERVER_REQUEST_OPERATION_NAME,
    )


def maybe_inject_tools_list_intent(response: Any) -> None:
    if config.mcp.capture_intent:
        _integration().inject_tools_list_response(response)


async def traced_call_tool(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    integration = _integration()

    span: Span = integration.trace(CLIENT_TOOL_CALL_OPERATION_NAME, submit_to_llmobs=True)

    try:
        result = await func(*args, **kwargs)

        if is_tool_error_result(result):
            content = getattr(result, "content", [])
            span.error = 1

            content_block = content[0] if content and isinstance(content, list) else None
            if content_block and getattr(content_block, "text", None):
                span.set_tag(ERROR_MSG, content_block.text)

        integration.llmobs_set_tags(
            span, args=list(args), kwargs=kwargs, response=result, operation=CLIENT_TOOL_CALL_OPERATION_NAME
        )

        return result
    except Exception:
        integration.llmobs_set_tags(
            span, args=list(args), kwargs=kwargs, response=None, operation=CLIENT_TOOL_CALL_OPERATION_NAME
        )
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


async def traced_client_session_initialize(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    integration = _integration()

    with integration.trace("%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True) as span:
        response = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            integration.llmobs_set_tags(span, args=list(args), kwargs=kwargs, response=response, operation="initialize")


async def traced_client_session_list_tools(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    integration = _integration()

    with integration.trace("%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True) as span:
        response = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            integration.llmobs_set_tags(span, args=list(args), kwargs=kwargs, response=response, operation="list_tools")


async def traced_client_session_aenter(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    integration = _integration()
    span = integration.trace(instance.__class__.__name__, submit_to_llmobs=True, type="client_session")

    setattr(instance, "_dd_span", span)
    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


async def traced_client_session_aexit(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    integration = _integration()
    span: Optional[Span] = getattr(instance, "_dd_span", None)

    try:
        return await func(*args, **kwargs)
    except Exception:
        if span:
            span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span:
            integration.llmobs_set_tags(
                span,
                args=[],
                kwargs=dict(
                    read_stream=_get_attr(instance, "_read_stream", None),
                    write_stream=_get_attr(instance, "_write_stream", None),
                ),
                response=None,
                operation="session",
            )
            span.finish()


def wrap_client_session(client_session: type) -> None:
    """Wrap the ClientSession methods traced the same way on every mcp version."""
    wrap(client_session, "__aenter__", traced_client_session_aenter)
    wrap(client_session, "__aexit__", traced_client_session_aexit)
    wrap(client_session, "call_tool", traced_call_tool)
    wrap(client_session, "list_tools", traced_client_session_list_tools)
    wrap(client_session, "initialize", traced_client_session_initialize)


def unwrap_client_session(client_session: type) -> None:
    unwrap(client_session, "__aenter__")
    unwrap(client_session, "__aexit__")
    unwrap(client_session, "call_tool")
    unwrap(client_session, "list_tools")
    unwrap(client_session, "initialize")
