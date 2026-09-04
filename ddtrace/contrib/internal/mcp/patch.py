import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
import weakref

import mcp


if TYPE_CHECKING:
    from mcp.types import ClientRequest
    from mcp.types import Request

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._integrations.mcp import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_REQUEST_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


log = get_logger(__name__)

_mcp2_servers: weakref.WeakSet[Any] = weakref.WeakSet()

config._add(
    "mcp",
    {
        "distributed_tracing": asbool(env.get("DD_MCP_DISTRIBUTED_TRACING", default=True)),
        "capture_intent": asbool(env.get("DD_MCP_CAPTURE_INTENT", default=False)),
    },
)


def get_version() -> str:
    from importlib.metadata import version

    return version("mcp")


def _supported_versions() -> dict[str, str]:
    return {"mcp": ">=1.10.0"}


def _set_distributed_headers_into_mcp_request(request: "ClientRequest") -> "ClientRequest":
    """Inject distributed tracing headers into MCP request metadata."""
    span = tracer.current_span()
    if span is None:
        return request

    headers = {}
    HTTPPropagator.inject(span.context, headers)
    if not headers:
        return request
    request_root = _get_attr(request, "root", None)
    request_is_root = request_root is None
    if request_is_root:
        request_root = request

    try:
        request_params = _get_attr(request_root, "params", None)
        if not request_params:
            return request

        # Use the `_meta` field to store tracing headers. It is accessed via a public
        # `meta` attribute on the request params. This field is reserved for server/clients
        # to attach additional metadata to a request. For more information, see:
        # https://modelcontextprotocol.io/specification/2025-06-18/basic#meta
        existing_meta = _get_attr(request_params, "meta", None)
        if existing_meta and hasattr(existing_meta, "model_dump"):
            meta_dict = existing_meta.model_dump()
        else:
            meta_dict = dict(existing_meta) if existing_meta else {}

        meta_dict["_dd_trace_context"] = headers
        params_dict = request_params.model_dump(by_alias=True)
        params_dict["_meta"] = meta_dict

        new_params = type(request_params)(**params_dict)
        request_dict = request_root.model_dump()
        request_dict["params"] = new_params

        new_request_root = type(request_root)(**request_dict)
        return new_request_root if request_is_root else type(request)(new_request_root)
    except Exception:
        log.error("Error injecting distributed tracing headers into MCP request metadata", exc_info=True)
        return request


def _extract_distributed_headers_from_mcp_request(request_root: "Request") -> Optional[dict[str, str]]:
    """Extract distributed tracing headers from MCP request params.meta field."""
    if isinstance(request_root, dict):
        params = request_root.get("params", request_root)
        meta = params.get("_meta", {}) if isinstance(params, dict) else {}
        headers = meta.get("_dd_trace_context", {}) if isinstance(meta, dict) else {}
        return headers if headers else None

    request_params = _get_attr(request_root, "params", None)
    meta = _get_attr(request_params, "meta", None) if request_params else None
    meta_dict = meta.model_dump() if meta and hasattr(meta, "model_dump") else {}
    headers = meta_dict.get("_dd_trace_context", {})
    return headers if headers else None


def traced_send_request(func, instance, args: tuple, kwargs: dict):
    """Injects distributed tracing headers into MCP request metadata"""
    if not args or not config.mcp.distributed_tracing:
        return func(*args, **kwargs)
    request = args[0]
    modified_request = _set_distributed_headers_into_mcp_request(request)
    return func(*((modified_request,) + args[1:]), **kwargs)


async def traced_call_tool(func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    span: Span = integration.trace(CLIENT_TOOL_CALL_OPERATION_NAME, submit_to_llmobs=True)

    try:
        result = await func(*args, **kwargs)
        if _get_attr(instance, "discover_result", None) is not None:
            integration.set_client_session_server_info(
                span, _get_attr(instance, "server_info", None), include_span=True
            )

        if getattr(result, "isError", getattr(result, "is_error", False)):
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


async def traced_client_session_initialize(func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    with integration.trace("%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True) as span:
        response = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response, operation="initialize")


async def traced_client_session_discover(func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    with integration.trace("%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True) as span:
        response = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response, operation="discover")


async def traced_client_session_list_tools(func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    with integration.trace("%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True) as span:
        response = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response, operation="list_tools")


async def traced_client_session_aenter(func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration
    span = integration.trace(instance.__class__.__name__, submit_to_llmobs=True, type="client_session")

    setattr(instance, "_dd_span", span)
    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


async def traced_client_session_aexit(func, instance, args: tuple, kwargs: dict):
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


async def traced_server_middleware(context, call_next):
    """Trace server requests handled by mcp 2's middleware pipeline."""
    integration: MCPIntegration = mcp._datadog_integration
    method = _get_attr(context, "method", "unknown")
    if method not in ("server/discover", "initialize", "tools/call", "tools/list"):
        return await call_next(context)

    # Tool schema injection is the only server-side handling needed for tools/list.
    # Keep this request untraced to preserve the existing span shape.
    if method == "tools/list":
        response = await call_next(context)
        if config.mcp.capture_intent:
            integration.inject_tools_list_response(response)
        return response

    params = _get_attr(context, "params", None)
    previous_context = tracer.context_provider.active()
    llmobs_context_provider = None
    previous_llmobs_context = None
    tracer_context_cleared = False
    llmobs_context_cleared = False
    span: Optional[Span] = None
    try:
        llmobs_context_provider = integration._get_llmobs_context_provider()
        previous_llmobs_context = llmobs_context_provider.active() if llmobs_context_provider else None
        # Preserve the ambient APM context for in-process requests without distributed headers.
        # Clear it when tracing is disabled or before activating an incoming context so the
        # extracted parent is not mistaken for an already-active trace.
        if not config.mcp.distributed_tracing:
            tracer.context_provider.activate(None)
            tracer_context_cleared = True
        if llmobs_context_provider:
            llmobs_context_provider.activate(None)
            llmobs_context_cleared = True
        if (
            method == "tools/call"
            and config.mcp.distributed_tracing
            and (headers := _extract_distributed_headers_from_mcp_request(params or {}))
        ):
            tracer.context_provider.activate(None)
            tracer_context_cleared = True
            activate_distributed_headers(tracer, config.mcp, headers)

        operation_name = SERVER_TOOL_CALL_OPERATION_NAME if method == "tools/call" else SERVER_REQUEST_OPERATION_NAME
        span = integration.trace(
            operation_name,
            submit_to_llmobs=True,
            span_name="mcp.{}".format(method),
        )

        if method == "tools/call":
            arguments = _get_attr(params, "arguments", None)
            integration.process_telemetry_arguments(span, arguments)

        response = await call_next(context)
        integration.llmobs_set_tags_server(span, context, response)
        return response
    except Exception:
        if span is not None:
            integration.llmobs_set_tags_server(span, context, None)
            span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span is not None:
            span.finish()
        if tracer_context_cleared:
            tracer.context_provider.activate(previous_context)
        if llmobs_context_provider and llmobs_context_cleared:
            llmobs_context_provider.activate(previous_llmobs_context)


def traced_server_init(func, instance, args: tuple, kwargs: dict):
    result = func(*args, **kwargs)
    if traced_server_middleware not in instance.middleware:
        instance.middleware.append(traced_server_middleware)
    _mcp2_servers.add(instance)
    return result


def traced_request_responder_enter(func, instance, args: tuple, kwargs: dict):
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
        activate_distributed_headers(tracer, config.mcp, headers)

    operation_name = (
        SERVER_TOOL_CALL_OPERATION_NAME if isinstance(request_root, CallToolRequest) else SERVER_REQUEST_OPERATION_NAME
    )

    span = integration.trace(
        operation_name,
        submit_to_llmobs=True,
        span_name="mcp.{}".format(_get_attr(request_root, "method", "unknown")),
    )
    setattr(instance, "_dd_span", span)

    if isinstance(request_root, CallToolRequest):
        integration.process_telemetry_argument(span, request_root)

    return func(*args, **kwargs)


def traced_request_responder_exit(func, instance, args: tuple, kwargs: dict):
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


async def traced_request_responder_respond(func, instance, args: tuple, kwargs: dict):
    from mcp.types import ListToolsResult

    response_arg = args[0] if len(args) > 0 else None
    response = getattr(response_arg, "root", None)
    integration: MCPIntegration = mcp._datadog_integration
    span: Optional[Span] = getattr(instance, "_dd_span", None)

    if config.mcp.capture_intent and isinstance(response, ListToolsResult):
        integration.inject_tools_list_response(response)

    try:
        return await func(*args, **kwargs)
    finally:
        if span:
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=dict(**kwargs, request_responder=instance),
                response=None,
                operation=SERVER_REQUEST_OPERATION_NAME,
            )


def patch():
    if getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = True
    mcp._datadog_integration = MCPIntegration(integration_config=config.mcp)

    from mcp.client.session import ClientSession

    is_mcp2 = False
    try:
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder
    except ImportError:
        is_mcp2 = True
        from mcp.server import Server

        wrap(ClientSession, "send_request", traced_send_request)
        wrap(Server, "__init__", traced_server_init)
    else:
        wrap(BaseSession, "send_request", traced_send_request)
        wrap(RequestResponder, "__enter__", traced_request_responder_enter)
        wrap(RequestResponder, "__exit__", traced_request_responder_exit)
        wrap(RequestResponder, "respond", traced_request_responder_respond)

    wrap(ClientSession, "__aenter__", traced_client_session_aenter)
    wrap(ClientSession, "__aexit__", traced_client_session_aexit)
    wrap(ClientSession, "call_tool", traced_call_tool)
    wrap(ClientSession, "list_tools", traced_client_session_list_tools)
    wrap(ClientSession, "initialize", traced_client_session_initialize)
    if is_mcp2:
        wrap(ClientSession, "send_discover", traced_client_session_discover)


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    from mcp.client.session import ClientSession

    is_mcp2 = False
    try:
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder
    except ImportError:
        is_mcp2 = True
        from mcp.server import Server

        unwrap(ClientSession, "send_request")
        unwrap(Server, "__init__")
        for server in _mcp2_servers:
            if traced_server_middleware in server.middleware:
                server.middleware.remove(traced_server_middleware)
        _mcp2_servers.clear()
    else:
        unwrap(BaseSession, "send_request")
        unwrap(RequestResponder, "__enter__")
        unwrap(RequestResponder, "__exit__")
        unwrap(RequestResponder, "respond")

    unwrap(ClientSession, "__aenter__")
    unwrap(ClientSession, "__aexit__")
    unwrap(ClientSession, "call_tool")
    unwrap(ClientSession, "list_tools")
    unwrap(ClientSession, "initialize")
    if is_mcp2:
        unwrap(ClientSession, "send_discover")

    delattr(mcp, "_datadog_integration")
