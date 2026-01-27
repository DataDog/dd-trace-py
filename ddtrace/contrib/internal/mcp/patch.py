import os
import sys
from typing import TYPE_CHECKING
from typing import Dict
from typing import Optional

import mcp


if TYPE_CHECKING:
    from mcp.types import ClientRequest
    from mcp.types import Request

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._integrations.mcp import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_REQUEST_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)

config._add(
    "mcp",
    {
        "distributed_tracing": asbool(os.getenv("DD_MCP_DISTRIBUTED_TRACING", default=True)),
        "capture_intent": asbool(os.getenv("DD_MCP_CAPTURE_INTENT", default=False)),
    },
)


def get_version() -> str:
    from importlib.metadata import version

    return version("mcp")


def _supported_versions() -> Dict[str, str]:
    return {"mcp": ">=1.10.0"}


def _set_distributed_headers_into_mcp_request(pin: Pin, request: "ClientRequest") -> "ClientRequest":
    """Inject distributed tracing headers into MCP request metadata."""
    span = pin.tracer.current_span()
    if span is None:
        return request

    headers = {}
    HTTPPropagator.inject(span.context, headers)
    if not headers:
        return request
    if _get_attr(request, "root", None) is None:
        return request

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

        meta_dict["_dd_trace_context"] = headers
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


def _extract_distributed_headers_from_mcp_request(request_root: "Request") -> Optional[Dict[str, str]]:
    """Extract distributed tracing headers from MCP request params.meta field."""
    request_params = _get_attr(request_root, "params", None)
    meta = _get_attr(request_params, "meta", None) if request_params else None
    meta_dict = meta.model_dump() if meta and hasattr(meta, "model_dump") else {}
    headers = meta_dict.get("_dd_trace_context", {})
    return headers if headers else None


@with_traced_module
def traced_send_request(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    """Injects distributed tracing headers into MCP request metadata"""
    if not args or not config.mcp.distributed_tracing:
        return func(*args, **kwargs)
    request = args[0]
    modified_request = _set_distributed_headers_into_mcp_request(pin, request)
    return func(*((modified_request,) + args[1:]), **kwargs)


@with_traced_module
async def traced_call_tool(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    span: Span = integration.trace(pin, CLIENT_TOOL_CALL_OPERATION_NAME, submit_to_llmobs=True)

    try:
        result = await func(*args, **kwargs)

        if getattr(result, "isError", False):
            content = getattr(result, "content", [])
            span.error = 1

            content_block = content[0] if content and isinstance(content, list) else None
            if content_block and getattr(content_block, "text", None):
                span.set_tag(ERROR_MSG, content_block.text)

        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=result, operation=CLIENT_TOOL_CALL_OPERATION_NAME
        )

        return result
    except Exception:
        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=None, operation=CLIENT_TOOL_CALL_OPERATION_NAME
        )
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


@with_traced_module
async def traced_client_session_initialize(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    with integration.trace(pin, "%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True) as span:
        response = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response, operation="initialize")


@with_traced_module
async def traced_client_session_list_tools(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    with integration.trace(pin, "%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True) as span:
        response = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response, operation="list_tools")


@with_traced_module
async def traced_client_session_aenter(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration
    span = integration.trace(pin, instance.__class__.__name__, submit_to_llmobs=True, type="client_session")

    setattr(instance, "_dd_span", span)
    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


@with_traced_module
async def traced_client_session_aexit(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration
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


@with_traced_module
def traced_request_responder_enter(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    from mcp.types import CallToolRequest
    from mcp.types import InitializeRequest

    integration: MCPIntegration = mcp._datadog_integration
    request_wrapper = _get_attr(instance, "request", None)
    request_root = _get_attr(request_wrapper, "root", None)

    # While this patch can trace all requests, we only trace these types right now
    if not request_root or (
        not isinstance(request_root, InitializeRequest) and not isinstance(request_root, CallToolRequest)
    ):
        return func(*args, **kwargs)

    # Activate distributed tracing if enabled for tool calls
    if (
        isinstance(request_root, CallToolRequest)
        and config.mcp.distributed_tracing
        and (headers := _extract_distributed_headers_from_mcp_request(request_root))
    ):
        activate_distributed_headers(pin.tracer, config.mcp, headers)

    operation_name = (
        SERVER_TOOL_CALL_OPERATION_NAME if isinstance(request_root, CallToolRequest) else SERVER_REQUEST_OPERATION_NAME
    )

    span = integration.trace(
        pin,
        operation_name,
        submit_to_llmobs=True,
        span_name="mcp.{}".format(_get_attr(request_root, "method", "unknown")),
    )
    setattr(instance, "_dd_span", span)

    if isinstance(request_root, CallToolRequest):
        integration.process_telemetry_argument(span, request_root)

    return func(*args, **kwargs)


@with_traced_module
def traced_request_responder_exit(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    span: Optional[Span] = getattr(instance, "_dd_span", None)
    if span:
        # Check if an exception occurred (__exit__ receives (exc_type, exc_val, exc_tb))
        exc_type = args[0] if len(args) > 0 else None
        exc_val = args[1] if len(args) > 1 else None
        exc_tb = args[2] if len(args) > 2 else None

        if exc_type is not None:
            span.set_exc_info(exc_type, exc_val, exc_tb)

        span.finish()
    return func(*args, **kwargs)


@with_traced_module
async def traced_request_responder_respond(mcp, pin: Pin, func, instance, args: tuple, kwargs: dict):
    from mcp.types import ListToolsResult

    response_arg = args[0] if len(args) > 0 else None
    response = getattr(response_arg, "root", None)
    integration: MCPIntegration = mcp._datadog_integration
    span: Optional[Span] = getattr(instance, "_dd_span", None)

    if config.mcp.capture_intent and isinstance(response, ListToolsResult):
        integration.inject_tools_list_response(response)

    if span:
        integration.llmobs_set_tags(
            span,
            args=args,
            kwargs=dict(**kwargs, request_responder=instance),
            response=None,
            operation=SERVER_REQUEST_OPERATION_NAME,
        )

    return await func(*args, **kwargs)


def patch():
    if getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = True
    Pin().onto(mcp)
    mcp._datadog_integration = MCPIntegration(integration_config=config.mcp)

    from mcp.client.session import ClientSession
    from mcp.shared.session import BaseSession
    from mcp.shared.session import RequestResponder

    wrap(ClientSession, "__aenter__", traced_client_session_aenter(mcp))
    wrap(ClientSession, "__aexit__", traced_client_session_aexit(mcp))
    wrap(BaseSession, "send_request", traced_send_request(mcp))
    wrap(ClientSession, "call_tool", traced_call_tool(mcp))
    wrap(ClientSession, "list_tools", traced_client_session_list_tools(mcp))
    wrap(ClientSession, "initialize", traced_client_session_initialize(mcp))
    wrap(RequestResponder, "__enter__", traced_request_responder_enter(mcp))
    wrap(RequestResponder, "__exit__", traced_request_responder_exit(mcp))
    wrap(RequestResponder, "respond", traced_request_responder_respond(mcp))


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    from mcp.client.session import ClientSession
    from mcp.shared.session import BaseSession
    from mcp.shared.session import RequestResponder

    unwrap(ClientSession, "__aenter__")
    unwrap(ClientSession, "__aexit__")
    unwrap(BaseSession, "send_request")
    unwrap(ClientSession, "call_tool")
    unwrap(ClientSession, "list_tools")
    unwrap(ClientSession, "initialize")
    unwrap(RequestResponder, "__enter__")
    unwrap(RequestResponder, "__exit__")
    unwrap(RequestResponder, "respond")

    delattr(mcp, "_datadog_integration")
