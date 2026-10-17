from __future__ import annotations

from collections.abc import Mapping
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
from ddtrace.llmobs._integrations.mcp import tool_result_is_error
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

    # Use the `_meta` field to store tracing headers. This field is reserved for
    # server/clients to attach additional metadata to a request. For more information, see:
    # https://modelcontextprotocol.io/specification/2025-06-18/basic#meta
    try:
        # mcp 1.x wraps every request in a ClientRequest root model, mcp 2.x sends it bare.
        if _get_attr(request, "root", None) is not None:
            return _set_headers_into_v1_request(request, headers)
        return _set_headers_into_v2_request(request, headers)
    except Exception:
        log.error("Error injecting distributed tracing headers into MCP request metadata", exc_info=True)
        return request


def _set_headers_into_v1_request(request: ClientRequest, headers: dict[str, str]) -> ClientRequest:
    request_params = _get_attr(request.root, "params", None)
    if not request_params:
        return request

    # `_meta` is exposed as a `meta` attribute holding a pydantic model.
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


def _set_headers_into_v2_request(request: Request, headers: dict[str, str]) -> Request:
    request_params = _get_attr(request, "params", None)
    if not request_params:
        return request

    # `_meta` is exposed as a `meta` attribute holding a plain dict. Copy rather than
    # mutate so the caller's request and meta objects are left untouched.
    meta_dict = dict(_get_attr(request_params, "meta", None) or {})
    meta_dict["_dd_trace_context"] = headers
    new_params = request_params.model_copy(update={"meta": meta_dict})
    return request.model_copy(update={"params": new_params})


def _extract_distributed_headers_from_mcp_request(request_root: Request) -> Optional[dict[str, str]]:
    """Extract distributed tracing headers from MCP request params.meta field."""
    request_params = _get_attr(request_root, "params", None)
    meta = _get_attr(request_params, "meta", None) if request_params else None
    meta_dict = meta.model_dump() if meta and hasattr(meta, "model_dump") else {}
    headers = meta_dict.get("_dd_trace_context", {})
    return headers if headers else None


def _extract_distributed_headers_from_params(params: Optional[Mapping[str, Any]]) -> Optional[dict[str, str]]:
    """Extract distributed tracing headers from the raw params of an mcp 2.x request.

    The params come straight off the wire and have not been validated yet, so any
    unexpected shape is ignored rather than failing the request.
    """
    meta = params.get("_meta") if isinstance(params, Mapping) else None
    headers = meta.get("_dd_trace_context") if isinstance(meta, Mapping) else None
    return headers if isinstance(headers, dict) and headers else None


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

        if tool_result_is_error(result):
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
        request_params = _get_attr(request_root, "params", None)
        integration.process_telemetry_argument(span, _get_attr(request_params, "arguments", None))

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


def traced_client_session_adopt(func, instance, args: tuple, kwargs: dict):
    """Tag the client session span with the server identity once the protocol is negotiated.

    mcp 2.x installs the negotiated state here for both the initialize handshake and the
    newer server/discover probe, which does not go through ClientSession.initialize.
    """
    integration: MCPIntegration = mcp._datadog_integration
    result = func(*args, **kwargs)
    session_span: Optional[Span] = getattr(instance, "_dd_span", None)
    try:
        integration.annotate_client_session_server_info(session_span, _get_attr(instance, "server_info", None))
    except Exception:
        # Tagging must never break the client's connection handshake.
        log.debug("Error tagging MCP client session with server info", exc_info=True)
    return result


def _set_tool_error_from_result(span: Span) -> None:
    """Mark a server tool call span as failed when the tool returned an error result.

    Uses the same tags the LLMObs tagging sets, so APM spans look the same whether or not
    LLMObs is enabled. The tool's own error text is recorded on the client span.
    """
    span.error = 1
    span.set_tag(ERROR_TYPE, "ToolError")
    span.set_tag(ERROR_MSG, "tool resulted in an error")


# Server requests traced on mcp 2.x, mapped to their operation names. Matches the
# requests traced on mcp 1.x by traced_request_responder_enter.
_TRACED_SERVER_METHODS = {
    "initialize": SERVER_REQUEST_OPERATION_NAME,
    "tools/call": SERVER_TOOL_CALL_OPERATION_NAME,
}


async def traced_server_runner_on_request(func, instance, args: tuple, kwargs: dict):
    """Trace a server request on mcp 2.x.

    ServerRunner._on_request is the single entry point for every inbound request, whatever
    the transport or protocol era. It receives the raw method and params and returns the
    result as a wire dict.
    """
    method = get_argument_value(args, kwargs, 1, "method", optional=True)
    params = get_argument_value(args, kwargs, 2, "params", optional=True)
    # The method is a string on the wire; anything else means the private SDK signature
    # changed, so pass through untraced rather than fail the request.
    operation_name = _TRACED_SERVER_METHODS.get(method) if isinstance(method, str) else None
    if operation_name is None:
        result = await func(*args, **kwargs)
        if config.mcp.capture_intent and method == "tools/list":
            mcp._datadog_integration.inject_tools_list_response(result)
        return result

    integration: MCPIntegration = mcp._datadog_integration
    is_tool_call = operation_name == SERVER_TOOL_CALL_OPERATION_NAME
    if (
        is_tool_call
        and config.mcp.distributed_tracing
        and (headers := _extract_distributed_headers_from_params(params))
    ):
        activate_distributed_headers(tracer, config.mcp, headers)

    span = integration.trace(operation_name, submit_to_llmobs=True, span_name=f"mcp.{method}")
    if is_tool_call and isinstance(params, Mapping):
        integration.process_telemetry_argument(span, params.get("arguments"))

    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    else:
        # mcp 2.x reports a failing tool as an error result rather than raising.
        if is_tool_call and tool_result_is_error(result):
            _set_tool_error_from_result(span)
        return result
    finally:
        dispatch_context = get_argument_value(args, kwargs, 0, "dctx", optional=True)
        integration.llmobs_set_tags(
            span,
            args=[],
            kwargs=dict(
                method=method,
                params=params,
                message_metadata=_get_attr(dispatch_context, "message_metadata", None),
            ),
            response=result,
            operation=operation_name,
        )
        span.finish()


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

    if _is_mcp_v2():
        from mcp.server.runner import ServerRunner

        wrap(ClientSession, "send_request", traced_send_request)
        wrap(ClientSession, "adopt", traced_client_session_adopt)
        # _on_request is private to the SDK and could be renamed in a 2.x release. Skip
        # only the server side then, so client tracing keeps working.
        if hasattr(ServerRunner, "_on_request"):
            wrap(ServerRunner, "_on_request", traced_server_runner_on_request)
        else:
            log.warning("mcp ServerRunner._on_request not found, MCP server requests will not be traced")
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

    if _is_mcp_v2():
        from mcp.server.runner import ServerRunner

        unwrap(ClientSession, "send_request")
        unwrap(ClientSession, "adopt")
        # Only wrapped when the SDK has it, see patch().
        if iswrapped(ServerRunner, "_on_request"):
            unwrap(ServerRunner, "_on_request")
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


def _is_mcp_v2() -> bool:
    """Whether the installed SDK uses the mcp 2.x dispatcher design.

    Checks for the server entry point that the 2.x path wraps, rather than for the absence
    of the 1.x session module, so a compatibility shim of mcp.shared.session cannot send
    patch() down the 1.x path.
    """
    from importlib.util import find_spec

    return find_spec("mcp.server.runner") is not None
