"""AI Guard checks for the client-managed MCP tools of the Anthropic SDK (anthropic.lib.tools.mcp).

A converted tool evaluates each call before tools/call is sent, with its MCP server when a known transport reached it.
"""

import asyncio
import functools
import inspect
import json
from types import ModuleType
from typing import Any
from typing import Optional

from ddtrace.aiguard._api_client import AIGuardAbortError
from ddtrace.aiguard._api_client import AIGuardClient
from ddtrace.aiguard._api_client import Function
from ddtrace.aiguard._api_client import MCPServer
from ddtrace.aiguard._api_client import Message
from ddtrace.aiguard._api_client import ToolCall
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard.integrations._mcp import capture_mcp_transports
from ddtrace.aiguard.integrations._mcp import client_session_server
from ddtrace.aiguard.integrations._mcp import mcp_reference
from ddtrace.aiguard.integrations._mcp import release_mcp_transports
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.native import uuid4
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


logger = ddlogger.get_logger(__name__)

_HELPERS_MODULE = "anthropic.lib.tools.mcp"

_client: Optional[AIGuardClient] = None
_wrapped_helpers: list[Any] = []


def _evaluate_tool_call(server: Optional[MCPServer], name: str, original_name: str, tool_input: Any) -> None:
    """Evaluate an MCP tool call as a lone assistant tool call, an ordinary one without server; fails open."""
    client = _client
    if client is None or not aiguard_config._ai_guard_mcp_enabled:
        return
    try:
        # The tool runner and direct calls do not pass the tool_use ID.
        tool_call = ToolCall(
            id=f"call_{uuid4().replace('-', '')}",
            function=Function(name=name, arguments=json.dumps(tool_input, default=str)),
        )
        servers: Optional[list[MCPServer]] = None
        if server is not None:
            entry = server.copy()
            entry["tools"] = {}
            tool_call["mcp"] = mcp_reference(entry, name, original_name)
            servers = [entry]
        evaluate_auto(
            client, [Message(role="assistant", tool_calls=[tool_call])], AI_GUARD.INTEGRATION_ANTHROPIC, servers
        )
    except AIGuardAbortError:
        # Raised bare: the tool runner turns any Exception, the SDK-compatible wrapper too, into a tool result.
        raise
    except Exception:
        logger.debug("AI Guard anthropic: failed to evaluate MCP tool call", exc_info=True)


async def _evaluate_tool_call_async(
    server: Optional[MCPServer], name: str, original_name: str, tool_input: Any
) -> None:
    if _client is None or not aiguard_config._ai_guard_mcp_enabled:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _evaluate_tool_call(server, name, original_name, tool_input)
    else:
        # A blocking HTTP call: run it off the event loop.
        await asyncio.to_thread(_evaluate_tool_call, server, name, original_name, tool_input)


def _guard_tool(tool: Any, mcp_tool: Any, session: Any) -> None:
    """Check each call of a converted tool, with the MCP server of its session when one is known."""
    if not aiguard_config._ai_guard_mcp_enabled:
        return
    original_name = getattr(mcp_tool, "name", None)
    if not isinstance(original_name, str):
        return
    server = client_session_server(session)

    # call() runs the validated original function captured at init, and tool(...) returns tool.func:
    # guarding both covers every way to run the tool, each with a single check.
    call, func = tool.call, tool.func
    if inspect.iscoroutinefunction(call):

        async def guarded_async_call(input: Any) -> Any:  # noqa: A002 (same signature as call)
            await _evaluate_tool_call_async(server, tool.name, original_name, input)
            return await call(input)

        @functools.wraps(func)
        async def guarded_async_func(*args: Any, **kwargs: Any) -> Any:
            await _evaluate_tool_call_async(server, tool.name, original_name, kwargs)
            return await func(*args, **kwargs)

        tool.call, tool.func = guarded_async_call, guarded_async_func
    else:

        def guarded_call(input: Any) -> Any:  # noqa: A002 (same signature as call)
            _evaluate_tool_call(server, tool.name, original_name, input)
            return call(input)

        @functools.wraps(func)
        def guarded_func(*args: Any, **kwargs: Any) -> Any:
            _evaluate_tool_call(server, tool.name, original_name, kwargs)
            return func(*args, **kwargs)

        tool.call, tool.func = guarded_call, guarded_func


def _convert_mcp_tool(f: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    tool = f(*args, **kwargs)
    try:
        _guard_tool(tool, args[0] if args else kwargs.get("tool"), args[1] if len(args) > 1 else kwargs.get("client"))
    except Exception:
        logger.debug("AI Guard anthropic: failed to guard a converted MCP tool", exc_info=True)
    return tool


def _wrap_helpers(module: ModuleType) -> None:
    for attr in ("mcp_tool", "async_mcp_tool"):
        try:
            function = getattr(module, attr)
            if not is_wrapped_with(function, _convert_mcp_tool):
                wrap(function, _convert_mcp_tool)
                _wrapped_helpers.append(function)
        except Exception:
            logger.debug("AI Guard anthropic: failed to wrap %s.%s", _HELPERS_MODULE, attr, exc_info=True)


def install_anthropic_mcp(client: AIGuardClient) -> None:
    """Wrap the Anthropic MCP helpers and the MCP SDK transports when imported (DD_AI_GUARD_MCP_ENABLED)."""
    global _client
    if not aiguard_config._ai_guard_mcp_enabled or _client is not None:
        return
    _client = client
    capture_mcp_transports()
    ModuleWatchdog.register_module_hook(_HELPERS_MODULE, _wrap_helpers)


def uninstall_anthropic_mcp() -> None:
    global _client
    if _client is None:
        return
    _client = None
    ModuleWatchdog.unregister_module_hook(_HELPERS_MODULE, _wrap_helpers)
    release_mcp_transports()
    while _wrapped_helpers:
        function = _wrapped_helpers.pop()
        try:
            unwrap(function, _convert_mcp_tool)
        except Exception:
            logger.debug("AI Guard anthropic: failed to unwrap %r", function, exc_info=True)
