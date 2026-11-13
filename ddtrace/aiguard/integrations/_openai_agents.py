"""AI Guard evaluation of MCP tool calls run by openai-agents MCP servers.

openai-agents converts the tools of each configured MCP server into function tools and runs the
ones the model selects through MCPUtil.invoke_mcp_tool, which knows the server, the original tool
name, the model-visible name and the model call ID. Applications can also call server.call_tool
directly. Both are evaluated before the tools/call request is sent; the contrib announces an
agent-driven call once, at the adapter, and dispatches the server.call_tool event for direct calls
only. Listeners run in a worker thread with a copy of the caller's context, so they keep no state.
"""

from collections.abc import Mapping
import json
from typing import Any
from typing import Optional

from ddtrace.aiguard._api_client import AIGuardAbortError
from ddtrace.aiguard._api_client import AIGuardClient
from ddtrace.aiguard._api_client import Function
from ddtrace.aiguard._api_client import Message
from ddtrace.aiguard._api_client import ToolCall
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._types import MCPServer
from ddtrace.aiguard._types import MCPTransport
from ddtrace.aiguard.integrations._mcp import clear_model_tool_calls
from ddtrace.aiguard.integrations._mcp import local_tool_call_id
from ddtrace.aiguard.integrations._mcp import mcp_reference
from ddtrace.aiguard.integrations._mcp import mcp_server
from ddtrace.aiguard.integrations._mcp import safe_stdio_args
from ddtrace.aiguard.integrations._mcp import tool_call_conversation
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.aiguard import aiguard_config


logger = ddlogger.get_logger(__name__)

# Matched on the class hierarchy so subclasses of the SDK servers keep their transport.
_TRANSPORT_BY_SERVER_CLASS: dict[str, MCPTransport] = {
    "MCPServerStdio": AI_GUARD.MCP_TRANSPORT_STDIO,
    "MCPServerSse": AI_GUARD.MCP_TRANSPORT_SSE,
    "MCPServerStreamableHttp": AI_GUARD.MCP_TRANSPORT_STREAMABLE_HTTP,
}
# Names the SDK generates when none is configured. They embed the stdio command or the raw URL,
# so they are never reported; the command and URL origin are reported through their own fields.
_GENERATED_NAME_PREFIXES = ("stdio: ", "sse: ", "streamable_http: ")


def _server_entry(server: Any) -> Optional[MCPServer]:
    """The mcp_servers entry of an SDK server; never headers, env, cwd or unsafe stdio args."""
    transport: MCPTransport = next(
        (
            _TRANSPORT_BY_SERVER_CLASS[cls.__name__]
            for cls in type(server).__mro__
            if cls.__name__ in _TRANSPORT_BY_SERVER_CLASS
        ),
        AI_GUARD.MCP_TRANSPORT_UNKNOWN,
    )
    try:
        name = server.name
    except Exception:
        name = None
    if not isinstance(name, str) or name.startswith(_GENERATED_NAME_PREFIXES):
        name = None
    params = getattr(server, "params", None)
    url = command = None
    args: list[str] = []
    if transport in (AI_GUARD.MCP_TRANSPORT_SSE, AI_GUARD.MCP_TRANSPORT_STREAMABLE_HTTP):
        candidate = params.get("url") if isinstance(params, Mapping) else None
        url = candidate if isinstance(candidate, str) else None
    elif transport == AI_GUARD.MCP_TRANSPORT_STDIO:
        # MCPServerStdio keeps its parameters as an mcp StdioServerParameters model.
        candidate = getattr(params, "command", None)
        command = candidate if isinstance(candidate, str) and candidate else None
        stdio_args = getattr(params, "args", None)
        if command and isinstance(stdio_args, (list, tuple)) and all(isinstance(arg, str) for arg in stdio_args):
            args = safe_stdio_args(command, stdio_args)
    return mcp_server("client", transport, name=name, url=url, command=command, args=args)


def _mcp_tool_call(
    server: Any, call_id: str, name: str, arguments: str, tool_name: str
) -> tuple[ToolCall, Optional[MCPServer]]:
    """The tool call of an MCP tool and its server entry; an unidentifiable server leaves an ordinary tool call."""
    tool_call = ToolCall(id=call_id, function=Function(name=name, arguments=arguments))
    try:
        entry = _server_entry(server)
    except Exception:
        logger.debug("Failed to read the openai-agents MCP server", exc_info=True)
        entry = None
    if entry is not None:
        tool_call["mcp"] = mcp_reference(entry, name, tool_name)
    return tool_call, entry


def _evaluate(client: AIGuardClient, messages: list[Message], mcp_servers: Optional[list[MCPServer]]) -> None:
    try:
        evaluate_auto(client, messages, AI_GUARD.INTEGRATION_OPENAI_AGENTS, mcp_servers)
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("Failed to evaluate openai-agents MCP tool call", exc_info=True)


def _openai_agents_turn_started() -> None:
    """Listener for openai_agents.turn.started: tool calls recorded in an earlier turn never describe this one's.

    Covers custom model providers, which never reach the OpenAI listeners that also clear them.
    """
    clear_model_tool_calls()


def _openai_agents_mcp_invoke_tool_before(
    client: AIGuardClient, server: Any, tool: Any, context: Any, input_json: Any, tool_display_name: Any
) -> None:
    """Listener for openai_agents.mcp.invoke_tool.before."""
    if not aiguard_config._ai_guard_mcp_enabled:
        return
    tool_name = getattr(tool, "name", None)
    if not isinstance(tool_name, str) or not tool_name:
        return
    call_id = getattr(context, "tool_call_id", None)
    model_name = tool_display_name or getattr(context, "tool_name", None) or tool_name
    tool_call, entry = _mcp_tool_call(
        server,
        call_id if isinstance(call_id, str) and call_id else local_tool_call_id(),
        str(model_name),
        input_json if isinstance(input_json, str) and input_json else "{}",
        tool_name,
    )
    _evaluate(client, *tool_call_conversation(tool_call, entry))


def _openai_agents_mcp_call_tool_before(client: AIGuardClient, server: Any, tool_name: Any, arguments: Any) -> None:
    """Listener for openai_agents.mcp.call_tool.before."""
    if not aiguard_config._ai_guard_mcp_enabled:
        return
    if not isinstance(tool_name, str) or not tool_name:
        return
    # Direct call: no model asked for it, so no history and an ID marked local.
    tool_call, entry = _mcp_tool_call(
        server, local_tool_call_id(), tool_name, json.dumps(arguments or {}, default=str), tool_name
    )
    _evaluate(client, [Message(role="assistant", tool_calls=[tool_call])], [entry] if entry else None)
