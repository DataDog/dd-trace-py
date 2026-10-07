from __future__ import annotations

import sys
from typing import TYPE_CHECKING
from typing import Optional

import mcp


if TYPE_CHECKING:
    from mcp.types import ClientRequest
    from mcp.types import Request

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.contrib.trace_utils import iswrapped
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._integrations.mcp import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_REQUEST_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.llmobs._integrations.mcp import is_tool_error_result
from ddtrace.llmobs._utils import _get_attr
from ddtrace.propagation.http import HTTPPropagator
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


def _set_distributed_headers_into_mcp_request(request: ClientRequest) -> ClientRequest:
    """Inject distributed tracing headers into MCP request metadata."""
    span = tracer.current_span()
    if span is None:
        return request

    headers = {}
    HTTPPropagator.inject(span.context, headers)
    if not headers:
        return request

    # mcp<2 wraps every request in a ClientRequest root model; mcp>=2 sends the request model itself.
    request_root = _get_attr(request, "root", None)
    inner_request = request if request_root is None else request_root

    try:
        request_params = _get_attr(inner_request, "params", None)
        if not request_params:
            return request

        # Use the `_meta` field to store tracing headers. It is accessed via a public
        # `meta` attribute on the request params. This field is reserved for server/clients
        # to attach additional metadata to a request. For more information, see:
        # https://modelcontextprotocol.io/specification/2025-06-18/basic#meta
        existing_meta = _get_attr(request_params, "meta", None)
        # mcp<2 models `meta` as a pydantic model, mcp>=2 as a TypedDict.
        if hasattr(existing_meta, "model_dump"):
            meta_dict = existing_meta.model_dump()
        else:
            meta_dict = dict(existing_meta) if existing_meta else {}

        meta_dict["_dd_trace_context"] = headers
        params_dict = request_params.model_dump(by_alias=True)
        params_dict["_meta"] = meta_dict

        new_params = type(request_params)(**params_dict)
        request_dict = inner_request.model_dump()
        request_dict["params"] = new_params

        new_inner_request = type(inner_request)(**request_dict)
        return new_inner_request if request_root is None else type(request)(new_inner_request)
    except Exception:
        log.error("Error injecting distributed tracing headers into MCP request metadata", exc_info=True)
        return request


def _extract_distributed_headers_from_mcp_request(request_root: Request) -> Optional[dict[str, str]]:
    """Extract distributed tracing headers from MCP request params.meta field."""
    request_params = _get_attr(request_root, "params", None)
    if isinstance(request_params, dict):
        # mcp>=2 servers receive the raw wire params, where the metadata keeps its `_meta` wire name.
        meta_dict = request_params.get("_meta")
    else:
        meta = _get_attr(request_params, "meta", None) if request_params else None
        meta_dict = meta.model_dump() if meta and hasattr(meta, "model_dump") else {}
    headers = meta_dict.get("_dd_trace_context") if isinstance(meta_dict, dict) else None
    return headers if headers and isinstance(headers, dict) else None


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

        if is_tool_error_result(result):
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


async def traced_server_runner_on_request(func, instance, args: tuple, kwargs: dict):
    """Traces server requests on mcp>=2, where ServerRunner._on_request replaces RequestResponder.

    Every server transport dispatches requests through this method with the raw wire method and params,
    and it returns the wire result dict.
    """
    dctx = get_argument_value(args, kwargs, 0, "dctx", optional=True)
    method = get_argument_value(args, kwargs, 1, "method", optional=True)
    params = get_argument_value(args, kwargs, 2, "params", optional=True)
    integration: MCPIntegration = mcp._datadog_integration

    if method == "tools/list":
        response = await func(*args, **kwargs)
        if config.mcp.capture_intent and isinstance(response, dict):
            integration.inject_tools_list_response(response)
        return response

    # While this patch can trace all requests, we only trace these types right now
    if method not in ("initialize", "tools/call"):
        return await func(*args, **kwargs)

    request = {"method": method, "params": params}
    is_tool_call = method == "tools/call"

    # Activate distributed tracing if enabled for tool calls
    if (
        is_tool_call
        and config.mcp.distributed_tracing
        and (headers := _extract_distributed_headers_from_mcp_request(request))
    ):
        activate_distributed_headers(tracer, config.mcp, headers)

    span = integration.trace(
        SERVER_TOOL_CALL_OPERATION_NAME if is_tool_call else SERVER_REQUEST_OPERATION_NAME,
        submit_to_llmobs=True,
        span_name="mcp.{}".format(method),
    )

    if is_tool_call:
        integration.process_telemetry_argument(span, request)

    response = None
    try:
        response = await func(*args, **kwargs)
        return response
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(
            span,
            args=[],
            kwargs=dict(request=request, message_metadata=_get_attr(dctx, "message_metadata", None)),
            response=response,
            operation=SERVER_REQUEST_OPERATION_NAME,
        )
        span.finish()


def _import_instrumented_classes():
    """Return (ClientSession, request sender, RequestResponder, ServerRunner) for the installed mcp.

    mcp 2.0 removed mcp.shared.session: ClientSession sends its own requests and the server dispatches
    through ServerRunner, so the classes that do not exist for the installed version are None.
    Raises ImportError when mcp is not the MCP SDK.
    """
    from mcp.client.session import ClientSession

    try:
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder
    except ImportError:
        from mcp.server.runner import ServerRunner

        return ClientSession, ClientSession, None, ServerRunner
    return ClientSession, BaseSession, RequestResponder, None


def patch():
    if getattr(mcp, "__datadog_patch", False):
        return

    # Claimed before the imports below so a concurrent patch() cannot clear the
    # guard above and wrap everything a second time.
    mcp.__datadog_patch = True

    try:
        client_session, request_sender, request_responder, server_runner = _import_instrumented_classes()
    except ImportError:
        mcp.__datadog_patch = False
        log.debug("mcp is importable but is not the MCP SDK, skipping instrumentation")
        return

    mcp._datadog_integration = MCPIntegration(integration_config=config.mcp)

    wrap(client_session, "__aenter__", traced_client_session_aenter)
    wrap(client_session, "__aexit__", traced_client_session_aexit)
    wrap(request_sender, "send_request", traced_send_request)
    wrap(client_session, "call_tool", traced_call_tool)
    wrap(client_session, "list_tools", traced_client_session_list_tools)
    wrap(client_session, "initialize", traced_client_session_initialize)

    if request_responder is not None:
        wrap(request_responder, "respond", traced_request_responder_respond)

        # RequestResponder gained the context manager protocol in mcp 1.3.0.
        if hasattr(request_responder, "__enter__") and hasattr(request_responder, "__exit__"):
            wrap(request_responder, "__enter__", traced_request_responder_enter)
            wrap(request_responder, "__exit__", traced_request_responder_exit)

    if server_runner is not None:
        wrap(server_runner, "_on_request", traced_server_runner_on_request)


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    # Only reachable with __datadog_patch set, which patch() leaves set only
    # when these imports succeeded, so they cannot fail here.
    client_session, request_sender, request_responder, server_runner = _import_instrumented_classes()

    unwrap(client_session, "__aenter__")
    unwrap(client_session, "__aexit__")
    unwrap(request_sender, "send_request")
    unwrap(client_session, "call_tool")
    unwrap(client_session, "list_tools")
    unwrap(client_session, "initialize")

    if request_responder is not None:
        unwrap(request_responder, "respond")

        # Only wrapped on mcp >= 1.3.0, see patch().
        if iswrapped(request_responder, "__enter__"):
            unwrap(request_responder, "__enter__")
            unwrap(request_responder, "__exit__")

    if server_runner is not None:
        unwrap(server_runner, "_on_request")

    delattr(mcp, "_datadog_integration")
