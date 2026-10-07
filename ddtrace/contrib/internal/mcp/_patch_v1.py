"""Instrumentation for mcp 1.x, where sessions and server requests are built on mcp.shared.session."""

from typing import Any
from typing import Callable
from typing import Optional

from mcp.client.session import ClientSession
from mcp.shared.session import BaseSession
from mcp.shared.session import RequestResponder
from mcp.types import CallToolRequest
from mcp.types import InitializeRequest
from mcp.types import ListToolsResult

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.contrib.internal.mcp._utils import activate_distributed_context
from ddtrace.contrib.internal.mcp._utils import inject_distributed_headers
from ddtrace.contrib.internal.mcp._utils import maybe_inject_tools_list_intent
from ddtrace.contrib.internal.mcp._utils import set_server_request_tags
from ddtrace.contrib.internal.mcp._utils import start_server_request_span
from ddtrace.contrib.internal.mcp._utils import unwrap_client_session
from ddtrace.contrib.internal.mcp._utils import wrap_client_session
from ddtrace.contrib.trace_utils import iswrapped
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.llmobs._utils import _get_attr


def _request_meta(request_root: Any) -> Optional[dict[str, Any]]:
    request_params = _get_attr(request_root, "params", None)
    meta = _get_attr(request_params, "meta", None) if request_params else None
    return meta.model_dump() if meta and hasattr(meta, "model_dump") else None


def traced_send_request(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Injects distributed tracing headers into MCP request metadata"""
    if not args or not config.mcp.distributed_tracing:
        return func(*args, **kwargs)
    request = args[0]
    # Every request is wrapped in a ClientRequest root model
    request_root = _get_attr(request, "root", None)
    if request_root is not None:
        new_root = inject_distributed_headers(request_root)
        if new_root is not request_root:
            request = type(request)(new_root)
    return func(*((request,) + args[1:]), **kwargs)


def traced_request_responder_enter(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    request_root = _get_attr(_get_attr(instance, "request", None), "root", None)

    if not isinstance(request_root, (InitializeRequest, CallToolRequest)):
        return func(*args, **kwargs)

    if isinstance(request_root, CallToolRequest):
        activate_distributed_context(_request_meta(request_root))

    setattr(instance, "_dd_span", start_server_request_span(request_root.method, request_root))
    return func(*args, **kwargs)


def traced_request_responder_exit(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    span: Optional[Span] = getattr(instance, "_dd_span", None)
    if span:
        # Check if an exception occurred (__exit__ receives (exc_type, exc_val, exc_tb))
        exc_type = args[0] if len(args) > 0 else None
        exc_val = args[1] if len(args) > 1 else None
        exc_tb = args[2] if len(args) > 2 else None

        if exc_type is not None and exc_val is not None:
            span.set_exc_info(exc_type, exc_val, exc_tb)

        span.finish()
    return func(*args, **kwargs)


async def traced_request_responder_respond(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    response_arg = args[0] if len(args) > 0 else None
    response = getattr(response_arg, "root", response_arg)
    span: Optional[Span] = getattr(instance, "_dd_span", None)

    if isinstance(response, ListToolsResult):
        maybe_inject_tools_list_intent(response)

    try:
        return await func(*args, **kwargs)
    finally:
        if span:
            set_server_request_tags(
                span,
                request=_get_attr(_get_attr(instance, "request", None), "root", None),
                response=response,
                message_metadata=_get_attr(instance, "message_metadata", None),
            )


def patch() -> None:
    wrap_client_session(ClientSession)
    wrap(BaseSession, "send_request", traced_send_request)
    wrap(RequestResponder, "respond", traced_request_responder_respond)

    # RequestResponder gained the context manager protocol in mcp 1.3.0.
    if hasattr(RequestResponder, "__enter__") and hasattr(RequestResponder, "__exit__"):
        wrap(RequestResponder, "__enter__", traced_request_responder_enter)
        wrap(RequestResponder, "__exit__", traced_request_responder_exit)


def unpatch() -> None:
    unwrap_client_session(ClientSession)
    unwrap(BaseSession, "send_request")
    unwrap(RequestResponder, "respond")

    # Only wrapped on mcp >= 1.3.0, see patch().
    if iswrapped(RequestResponder, "__enter__"):
        unwrap(RequestResponder, "__enter__")
        unwrap(RequestResponder, "__exit__")
