from __future__ import annotations

import dataclasses
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
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._integrations.mcp import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import DD_TRACE_CONTEXT_META_KEY
from ddtrace.llmobs._integrations.mcp import SERVER_MIDDLEWARE_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_REQUEST_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import TELEMETRY_KEY
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.llmobs._integrations.mcp import get_tool_error_message
from ddtrace.llmobs._integrations.mcp import is_tool_error
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
    if _get_attr(request, "root", None) is None:
        return _set_distributed_headers_into_mcp_v2_request(request, headers)

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


def _set_distributed_headers_into_mcp_v2_request(request, headers: dict[str, str]):
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


def _extract_distributed_headers_from_mcp_request(request_root: Request) -> Optional[dict[str, str]]:
    """Extract distributed tracing headers from MCP request params.meta field."""
    request_params = _get_attr(request_root, "params", None)
    meta = _get_attr(request_params, "meta", None) if request_params else None
    meta_dict = meta.model_dump() if meta and hasattr(meta, "model_dump") else {}
    headers = meta_dict.get(DD_TRACE_CONTEXT_META_KEY, {})
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
            integration.llmobs_set_tags(
                span, args=args, kwargs=dict(kwargs, session=instance), response=response, operation=func.__name__
            )


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


class _DatadogServerMiddleware:
    """mcp>=2 server middleware. Every transport and protocol version runs each inbound
    message through Server.middleware, so this replaces the RequestResponder patches used for mcp<2.
    """

    async def __call__(self, ctx, call_next):
        integration: Optional[MCPIntegration] = getattr(mcp, "_datadog_integration", None)
        method = ctx.method
        if integration is None or ctx.request_id is None:
            return await call_next(ctx)

        if method == "tools/list":
            result = await call_next(ctx)
            if config.mcp.capture_intent:
                integration.inject_tools_list_response(result)
            return result

        # While this middleware sees all requests, we only trace these types right now
        if method not in ("initialize", "tools/call"):
            return await call_next(ctx)

        if (
            method == "tools/call"
            and config.mcp.distributed_tracing
            and (headers := (ctx.meta or {}).get(DD_TRACE_CONTEXT_META_KEY))
        ):
            activate_distributed_headers(tracer, config.mcp, headers)

        operation_name = SERVER_TOOL_CALL_OPERATION_NAME if method == "tools/call" else SERVER_REQUEST_OPERATION_NAME
        span = integration.trace(operation_name, submit_to_llmobs=True, span_name=f"mcp.{method}")

        if method == "tools/call":
            ctx = _strip_telemetry_argument(integration, span, ctx)

        result = None
        try:
            result = await call_next(ctx)
            return result
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            integration.llmobs_set_tags(
                span, args=[], kwargs={"ctx": ctx}, response=result, operation=SERVER_MIDDLEWARE_OPERATION_NAME
            )
            span.finish()


def _strip_telemetry_argument(integration: MCPIntegration, span: Span, ctx):
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


def traced_server_init(func, instance, args: tuple, kwargs: dict):
    result = func(*args, **kwargs)
    # Outermost, so the span also covers params validation and the SDK's OpenTelemetry middleware
    instance.middleware.insert(0, _DatadogServerMiddleware())
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

    try:
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder
    except ImportError:
        # mcp>=2 removed mcp.shared.session
        from mcp.server.lowlevel.server import Server

        wrap(ClientSession, "send_request", traced_send_request)
        wrap(ClientSession, "discover", traced_client_session_initialize)
        wrap(Server, "__init__", traced_server_init)
        return

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

    # Only wrapped on mcp>=2, see patch().
    if iswrapped(ClientSession, "discover"):
        from mcp.server.lowlevel.server import Server

        unwrap(ClientSession, "send_request")
        unwrap(ClientSession, "discover")
        unwrap(Server, "__init__")
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
