"""Instrumentation for mcp 2.x, which removed mcp.shared.session.

ClientSession sends its own requests, and every server transport dispatches requests through
ServerRunner._on_request with the raw wire method and params, returning the wire result dict.
"""

import sys
from typing import Any
from typing import Callable

from mcp.client.session import ClientSession
from mcp.server.runner import ServerRunner

from ddtrace import config
from ddtrace.contrib.internal.mcp._utils import TRACED_SERVER_METHODS
from ddtrace.contrib.internal.mcp._utils import activate_distributed_context
from ddtrace.contrib.internal.mcp._utils import inject_distributed_headers
from ddtrace.contrib.internal.mcp._utils import maybe_inject_tools_list_intent
from ddtrace.contrib.internal.mcp._utils import set_server_request_tags
from ddtrace.contrib.internal.mcp._utils import start_server_request_span
from ddtrace.contrib.internal.mcp._utils import unwrap_client_session
from ddtrace.contrib.internal.mcp._utils import wrap_client_session
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._utils import _get_attr


def traced_send_request(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Injects distributed tracing headers into MCP request metadata"""
    if not args or not config.mcp.distributed_tracing:
        return func(*args, **kwargs)
    return func(*((inject_distributed_headers(args[0]),) + args[1:]), **kwargs)


async def traced_server_runner_on_request(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    dctx = get_argument_value(args, kwargs, 0, "dctx", optional=True)
    method = get_argument_value(args, kwargs, 1, "method", optional=True)
    params = get_argument_value(args, kwargs, 2, "params", optional=True)

    if method == "tools/list":
        response = await func(*args, **kwargs)
        if isinstance(response, dict):
            maybe_inject_tools_list_intent(response)
        return response

    if method not in TRACED_SERVER_METHODS:
        return await func(*args, **kwargs)

    request = {"method": method, "params": params}
    if method == "tools/call" and isinstance(params, dict):
        # The wire params keep the `_meta` name the client serialized the metadata under
        activate_distributed_context(params.get("_meta"))

    span = start_server_request_span(method, request)
    response = None
    try:
        response = await func(*args, **kwargs)
        return response
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        set_server_request_tags(
            span, request=request, response=response, message_metadata=_get_attr(dctx, "message_metadata", None)
        )
        span.finish()


def patch() -> None:
    wrap_client_session(ClientSession)
    wrap(ClientSession, "send_request", traced_send_request)
    wrap(ServerRunner, "_on_request", traced_server_runner_on_request)


def unpatch() -> None:
    unwrap_client_session(ClientSession)
    unwrap(ClientSession, "send_request")
    unwrap(ServerRunner, "_on_request")
