"""AI Guard checks for the client-managed MCP tools of the Anthropic SDK (anthropic.lib.tools.mcp).

A converted tool checks each call before tools/call is sent, unless its response was already evaluated.
"""

from _thread import get_ident
import asyncio
from collections.abc import Mapping
from contextvars import ContextVar
import inspect
import json
from types import ModuleType
from typing import Any
from typing import NamedTuple
from typing import Optional
from weakref import WeakValueDictionary

from ddtrace.aiguard._api_client import AIGuardAbortError
from ddtrace.aiguard._api_client import AIGuardClient
from ddtrace.aiguard._api_client import Function
from ddtrace.aiguard._api_client import MCPServer
from ddtrace.aiguard._api_client import Message
from ddtrace.aiguard._api_client import ToolCall
from ddtrace.aiguard._common import _get
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard.integrations._mcp import capture_mcp_transports
from ddtrace.aiguard.integrations._mcp import client_session_server
from ddtrace.aiguard.integrations._mcp import mcp_reference
from ddtrace.aiguard.integrations._mcp import release_mcp_transports
from ddtrace.internal import span_bus
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.native import uuid4
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


logger = ddlogger.get_logger(__name__)

_HELPERS_MODULE = "anthropic.lib.tools.mcp"
_BETA_MESSAGES_MODULE = "anthropic.resources.beta.messages.messages"
_TOOL_ATTR = "_dd_aiguard_mcp"
# Operation name of the span the Anthropic contrib opens around a messages request.
_PROVIDER_SPAN_NAME = "anthropic.request"


class MCPTool(NamedTuple):
    """MCP metadata of a converted tool."""

    server: MCPServer
    original_name: str


# Converted MCP tools by the id of their input_schema, which every to_dict() copy of them shares.
_tools_by_schema: "WeakValueDictionary[int, Any]" = WeakValueDictionary()
_client: Optional[AIGuardClient] = None
_wrapped_helpers: list[Any] = []
# [owner task or thread, root span key, (tool, arguments) of the MCP tool calls of the last evaluated response],
# one holder per request; updated in place so an evaluation in asyncio.to_thread reaches the caller's context.
_evaluated_calls: "ContextVar[Optional[list[Any]]]" = ContextVar("aiguard_mcp_evaluated_calls", default=None)


def _owner() -> Any:
    """Current asyncio task, else the current thread: only the owner of a holder consumes its marks."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return task if task is not None else get_ident()


def _trace_key() -> Optional[int]:
    """span_id of the active root span; None without one or when it is the Anthropic request span itself."""
    root = span_bus.get_root_span()
    # Matched by name: span_type is only llm with LLM Obs, and the component tag is removed.
    if root is None or root.name == _PROVIDER_SPAN_NAME:
        return None
    return root.span_id


def _tool_arguments(tool_input: Any) -> str:
    # Same serialization as the tool_use converter, so a response tool call matches the later call.
    return json.dumps(tool_input, default=str)


def _tool_call_to_check(tool: Any, tool_input: Any) -> Optional[str]:
    """Arguments of an MCP tool call to evaluate; None when checks are off or its response was evaluated."""
    if not aiguard_config._ai_guard_mcp_enabled or _client is None:
        return None
    try:
        arguments = _tool_arguments(tool_input)
        holder = _evaluated_calls.get()
        if holder is not None and holder[2] and holder[0] == _owner() and holder[1] == _trace_key():
            try:
                holder[2].remove((tool, arguments))
                return None
            except ValueError:
                pass
        return arguments
    except Exception:
        logger.debug("AI Guard anthropic: failed to prepare MCP tool call check", exc_info=True)
        return None


def _evaluate_tool_call(tool: Any, arguments: str) -> None:
    """Evaluate a direct MCP tool call as a lone assistant tool call."""
    client = _client
    if client is None:
        return
    try:
        info: MCPTool = getattr(tool, _TOOL_ATTR)
        server = info.server.copy()
        server["tools"] = {}
        # No provider ID exists for a call made outside a model response.
        tool_call = ToolCall(
            id=f"call_{uuid4().replace('-', '')}", function=Function(name=tool.name, arguments=arguments)
        )
        tool_call["mcp"] = mcp_reference(server, tool.name, info.original_name)
        evaluate_auto(
            client, [Message(role="assistant", tool_calls=[tool_call])], AI_GUARD.INTEGRATION_ANTHROPIC, [server]
        )
    except AIGuardAbortError:
        # Raised bare: the tool runner turns any Exception, the SDK-compatible wrapper too, into a tool result.
        raise
    except Exception:
        logger.debug("AI Guard anthropic: failed to evaluate MCP tool call", exc_info=True)


def _guard_tool(tool: Any, mcp_tool: Any, session: Any) -> None:
    """Attach the MCP metadata of session to a converted tool and check its calls; no-op if unknown."""
    if not aiguard_config._ai_guard_mcp_enabled:
        return
    server = client_session_server(session)
    original_name = getattr(mcp_tool, "name", None)
    if server is None or not isinstance(original_name, str) or not isinstance(tool.input_schema, Mapping):
        return
    # A copy owned by this tool, so its to_dict() params resolve to it and to no other tool.
    tool.input_schema = dict(tool.input_schema)
    setattr(tool, _TOOL_ATTR, MCPTool(server, original_name))
    _tools_by_schema[id(tool.input_schema)] = tool

    call = tool.call
    if inspect.iscoroutinefunction(call):

        async def guarded_async_call(input: Any) -> Any:  # noqa: A002 (same signature as call)
            arguments = _tool_call_to_check(tool, input)
            if arguments is not None:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    _evaluate_tool_call(tool, arguments)
                else:
                    # A blocking HTTP call: run it off the event loop.
                    await asyncio.to_thread(_evaluate_tool_call, tool, arguments)
            return await call(input)

        tool.call = guarded_async_call
    else:

        def guarded_call(input: Any) -> Any:  # noqa: A002 (same signature as call)
            arguments = _tool_call_to_check(tool, input)
            if arguments is not None:
                _evaluate_tool_call(tool, arguments)
            return call(input)

        tool.call = guarded_call


def _convert_mcp_tool(f: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    tool = f(*args, **kwargs)
    try:
        _guard_tool(tool, args[0] if args else kwargs.get("tool"), args[1] if len(args) > 1 else kwargs.get("client"))
    except Exception:
        logger.debug("AI Guard anthropic: failed to attach MCP metadata to a converted tool", exc_info=True)
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


def _reset_on_parse(f: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    # messages.parse (the non-streaming tool runner) fires no hook, so it ends the previous operation here.
    if _evaluated_calls.get() is not None:
        _evaluated_calls.set(None)
    return f(*args, **kwargs)


def _wrap_parse(module: ModuleType) -> None:
    for owner in ("Messages", "AsyncMessages"):
        try:
            function = getattr(module, owner).parse
            if not is_wrapped_with(function, _reset_on_parse):
                wrap(function, _reset_on_parse)
                _wrapped_helpers.append(function)
        except Exception:
            logger.debug("AI Guard anthropic: failed to wrap %s.%s.parse", _BETA_MESSAGES_MODULE, owner, exc_info=True)


def install_anthropic_mcp(client: AIGuardClient) -> None:
    """Wrap the Anthropic MCP helpers and the MCP SDK transports when imported (DD_AI_GUARD_MCP_ENABLED)."""
    global _client
    if not aiguard_config._ai_guard_mcp_enabled or _client is not None:
        return
    _client = client
    capture_mcp_transports()
    ModuleWatchdog.register_module_hook(_HELPERS_MODULE, _wrap_helpers)
    ModuleWatchdog.register_module_hook(_BETA_MESSAGES_MODULE, _wrap_parse)


def uninstall_anthropic_mcp() -> None:
    global _client
    if _client is None:
        return
    _client = None
    ModuleWatchdog.unregister_module_hook(_HELPERS_MODULE, _wrap_helpers)
    ModuleWatchdog.unregister_module_hook(_BETA_MESSAGES_MODULE, _wrap_parse)
    release_mcp_transports()
    while _wrapped_helpers:
        function = _wrapped_helpers.pop()
        try:
            unwrap(function, _reset_on_parse if is_wrapped_with(function, _reset_on_parse) else _convert_mcp_tool)
        except Exception:
            logger.debug("AI Guard anthropic: failed to unwrap %r", function, exc_info=True)


def request_mcp_tools(tools: Any) -> dict[str, Any]:
    """Converted MCP tools of a request by model-facing name, resolved from its tools param."""
    if not _tools_by_schema or not aiguard_config._ai_guard_mcp_enabled or not isinstance(tools, (list, tuple)):
        return {}
    result = {}
    for param in tools:
        schema = _get(param, "input_schema")
        tool = _tools_by_schema.get(id(schema)) if schema is not None else None
        name = _get(param, "name")
        if tool is not None and tool.input_schema is schema and name == tool.name:
            result[name] = tool
    return result


def attach_mcp(messages: list[Message], tools: dict[str, Any]) -> Optional[list[MCPServer]]:
    """Reference the server of each MCP tool call in messages; returns the mcp_servers payload or None."""
    if not tools:
        return None
    servers: dict[str, MCPServer] = {}
    for message in messages:
        for tool_call in message.get("tool_calls") or []:
            name = tool_call["function"]["name"]
            tool = tools.get(name)
            if tool is None:
                continue
            info: MCPTool = getattr(tool, _TOOL_ATTR)
            server = servers.get(info.server["id"])
            if server is None:
                server = servers[info.server["id"]] = info.server.copy()
                server["tools"] = {}
            tool_call["mcp"] = mcp_reference(server, name, info.original_name)
    return list(servers.values()) or None


def start_request() -> None:
    """Give the current request its own holder, so sibling tasks never share or consume its marks."""
    _evaluated_calls.set([_owner(), None, []])


def mark_evaluated(messages: list[Message], tools: dict[str, Any]) -> None:
    """Replace the evaluated MCP tool calls of this request with those of messages; [] clears them."""
    evaluated_calls: list[tuple[Any, str]] = []
    try:
        for message in messages:
            for tool_call in message.get("tool_calls") or []:
                tool = tools.get(tool_call["function"]["name"])
                if tool is not None and "mcp" in tool_call:
                    evaluated_calls.append((tool, tool_call["function"]["arguments"]))
        key = _trace_key()
    except Exception:
        logger.debug("AI Guard anthropic: failed to record evaluated MCP tool calls", exc_info=True)
        evaluated_calls, key = [], None
    holder = _evaluated_calls.get()
    owner = _owner()
    # In place only for the holder's owner or a worker thread evaluating for its task (asyncio.to_thread).
    if holder is not None and (holder[0] == owner or (isinstance(holder[0], asyncio.Task) and isinstance(owner, int))):
        holder[1:] = [key, evaluated_calls]
    else:
        _evaluated_calls.set([owner, key, evaluated_calls])
