from __future__ import annotations

import sys
from typing import Optional

import mcp

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.mcp._constants import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_REQUEST_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.contrib.internal.mcp._tool_result import DEFAULT_TOOL_ERROR_MESSAGE
from ddtrace.contrib.internal.mcp._tool_result import get_tool_error_message
from ddtrace.contrib.internal.mcp._tool_result import is_tool_error
from ddtrace.contrib.internal.mcp.utils import DatadogServerMiddleware
from ddtrace.contrib.internal.mcp.utils import extract_distributed_headers_from_mcp_request
from ddtrace.contrib.internal.mcp.utils import set_distributed_headers_into_mcp_request
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.contrib.trace_utils import iswrapped
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.trace import tracer


log = get_logger(__name__)

config._add(
    "mcp",
    {
        "distributed_tracing": asbool(env.get("DD_MCP_DISTRIBUTED_TRACING", default=True)),
        "capture_intent": asbool(env.get("DD_MCP_CAPTURE_INTENT", default=False)),
    },
)


def get_version() -> str:
    from importlib.metadata import version

    try:
        return version("mcp")
    except Exception:
        return ""


def _supported_versions() -> dict[str, str]:
    return {"mcp": ">=1.10.0"}


MCP_VERSION = parse_version(get_version())


def traced_send_request(func, instance, args: tuple, kwargs: dict):
    """Injects distributed tracing headers into MCP request metadata"""
    if not args or not config.mcp.distributed_tracing:
        return func(*args, **kwargs)
    request = args[0]
    modified_request = set_distributed_headers_into_mcp_request(request)
    return func(*((modified_request,) + args[1:]), **kwargs)


async def traced_call_tool(func, instance, args: tuple, kwargs: dict):
    integration: MCPIntegration = mcp._datadog_integration

    span: Span = integration.trace(CLIENT_TOOL_CALL_OPERATION_NAME, submit_to_llmobs=True)

    try:
        result = await func(*args, **kwargs)

        if is_tool_error(result):
            span.error = 1
            error_message = get_tool_error_message(result)
            if error_message:
                span.set_tag(ERROR_MSG, error_message)

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
        and (headers := extract_distributed_headers_from_mcp_request(request_root))
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
    from mcp.types import CallToolResult
    from mcp.types import ListToolsResult

    response_arg = args[0] if args else kwargs.get("response")
    response = getattr(response_arg, "root", response_arg)
    integration: MCPIntegration = mcp._datadog_integration
    span: Optional[Span] = getattr(instance, "_dd_span", None)

    if config.mcp.capture_intent and isinstance(response, ListToolsResult):
        integration.inject_tools_list_response(response)

    try:
        return await func(*args, **kwargs)
    finally:
        if span:
            if isinstance(response, CallToolResult) and response.isError:
                span.error = 1
                span.set_tag(ERROR_TYPE, "ToolError")
                span.set_tag(ERROR_MSG, get_tool_error_message(response) or DEFAULT_TOOL_ERROR_MESSAGE)
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=dict(**kwargs, request_responder=instance),
                response=None,
                operation=SERVER_REQUEST_OPERATION_NAME,
            )


def traced_client_session_adopt(func, instance, args: tuple, kwargs: dict):
    """Tags the client session span with the server's name on stateless mcp>=2 sessions, which skip
    initialize. Their server info arrives in a discover request's DiscoverResult, which every stateless
    setup path passes to adopt(). A Client pinned with mode="2026-07-28" skips discover and adopts a
    placeholder with no server info (unless given prior_discover), so it gets no mcp_server_name.
    """
    result = func(*args, **kwargs)
    integration: MCPIntegration = mcp._datadog_integration
    span = getattr(instance, "_dd_span", None) or tracer.current_span()
    integration.set_client_session_server_info(span, _get_attr(instance, "server_info", None))
    return result


def traced_server_runner_init(func, instance, args: tuple, kwargs: dict):
    """Every mcp>=2 transport builds a ServerRunner per connection, which reads Server.middleware
    at call time. Hooking here also covers servers constructed before patch().
    """
    result = func(*args, **kwargs)
    try:
        middleware = instance.server.middleware
        if not any(isinstance(m, DatadogServerMiddleware) for m in middleware):
            # Outermost, so the span also covers params validation and the SDK's OpenTelemetry middleware
            middleware.insert(0, DatadogServerMiddleware())
    except Exception:
        log.debug("Error installing Datadog MCP server middleware", exc_info=True)
    return result


def patch():
    if getattr(mcp, "__datadog_patch", False):
        return

    # Claimed before the imports below so a concurrent patch() cannot clear the
    # guard above and wrap everything a second time.
    mcp.__datadog_patch = True

    try:
        from mcp.client.session import ClientSession
    except ImportError:
        mcp.__datadog_patch = False
        log.debug("mcp is importable but is not the MCP SDK, skipping instrumentation")
        return

    mcp._datadog_integration = MCPIntegration(integration_config=config.mcp)

    wrap(ClientSession, "__aenter__", traced_client_session_aenter)
    wrap(ClientSession, "__aexit__", traced_client_session_aexit)
    wrap(ClientSession, "call_tool", traced_call_tool)
    wrap(ClientSession, "list_tools", traced_client_session_list_tools)
    wrap(ClientSession, "initialize", traced_client_session_initialize)

    if MCP_VERSION >= (2, 0, 0):
        from mcp.server.runner import ServerRunner

        wrap(ClientSession, "send_request", traced_send_request)
        wrap(ClientSession, "adopt", traced_client_session_adopt)
        wrap(ServerRunner, "__init__", traced_server_runner_init)
        return

    from mcp.shared.session import BaseSession
    from mcp.shared.session import RequestResponder

    wrap(BaseSession, "send_request", traced_send_request)
    wrap(RequestResponder, "respond", traced_request_responder_respond)

    # RequestResponder gained the context manager protocol in mcp 1.3.0.
    if hasattr(RequestResponder, "__enter__") and hasattr(RequestResponder, "__exit__"):
        wrap(RequestResponder, "__enter__", traced_request_responder_enter)
        wrap(RequestResponder, "__exit__", traced_request_responder_exit)


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    # Only reachable with __datadog_patch set, which patch() leaves set only
    # when this import succeeded, so it cannot fail here.
    from mcp.client.session import ClientSession

    unwrap(ClientSession, "__aenter__")
    unwrap(ClientSession, "__aexit__")
    unwrap(ClientSession, "call_tool")
    unwrap(ClientSession, "list_tools")
    unwrap(ClientSession, "initialize")

    if MCP_VERSION >= (2, 0, 0):
        from mcp.server.runner import ServerRunner

        unwrap(ClientSession, "send_request")
        unwrap(ClientSession, "adopt")
        unwrap(ServerRunner, "__init__")
    else:
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder

        unwrap(BaseSession, "send_request")
        unwrap(RequestResponder, "respond")

        # Only wrapped on mcp >= 1.3.0, see patch().
        if iswrapped(RequestResponder, "__enter__"):
            unwrap(RequestResponder, "__enter__")
            unwrap(RequestResponder, "__exit__")

    delattr(mcp, "_datadog_integration")
