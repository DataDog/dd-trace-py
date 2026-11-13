"""AI Guard evaluation of MCP tool calls run by openai-agents MCP servers.

openai-agents converts the tools of each configured MCP server into function tools and runs the
ones the model selects through MCPUtil.invoke_mcp_tool, which knows the server, the original tool
name, the model-visible name and the model call ID. Applications can also call server.call_tool
directly. Both are evaluated before the tools/call request is sent; the contrib announces an
agent-driven call once, at the adapter, and dispatches the server.call_tool event for direct calls
only. Each call is evaluated on its own: the model response that selected it is evaluated, with its
conversation, by the model provider's listener.
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
from ddtrace.aiguard.integrations._mcp import client_mcp_server
from ddtrace.aiguard.integrations._mcp import local_tool_call_id
from ddtrace.aiguard.integrations._mcp import mcp_reference
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
    """The mcp_servers entry of an SDK server, from the parameters it was configured with."""
    transport: MCPTransport = next(
        (
            _TRANSPORT_BY_SERVER_CLASS[cls.__name__]
            for cls in type(server).__mro__
            if cls.__name__ in _TRANSPORT_BY_SERVER_CLASS
        ),
        AI_GUARD.MCP_TRANSPORT_UNKNOWN,
    )
    name = getattr(server, "name", None)
    if isinstance(name, str) and name.startswith(_GENERATED_NAME_PREFIXES):
        name = None
    params = getattr(server, "params", None)
    if transport == AI_GUARD.MCP_TRANSPORT_STDIO:
        # MCPServerStdio keeps its parameters as an mcp StdioServerParameters model.
        return client_mcp_server(
            transport, name=name, command=getattr(params, "command", None), args=getattr(params, "args", None)
        )
    url = params.get("url") if transport != AI_GUARD.MCP_TRANSPORT_UNKNOWN and isinstance(params, Mapping) else None
    return client_mcp_server(transport, name=name, url=url)


def _evaluate_mcp_call(
    client: AIGuardClient, server: Any, call_id: str, name: str, arguments: str, tool_name: str
) -> None:
    """Evaluate one MCP tool call with its server entry; a server that cannot be identified leaves an ordinary call."""
    tool_call = ToolCall(id=call_id, function=Function(name=name, arguments=arguments))
    try:
        entry = _server_entry(server)
    except Exception:
        logger.debug("Failed to read the openai-agents MCP server", exc_info=True)
        entry = None
    if entry is not None:
        tool_call["mcp"] = mcp_reference(entry, name, tool_name)
    messages = [Message(role="assistant", tool_calls=[tool_call])]
    evaluate_auto(client, messages, AI_GUARD.INTEGRATION_OPENAI_AGENTS, [entry] if entry else None)


def _openai_agents_mcp_invoke_tool_before(
    client: AIGuardClient, server: Any, tool: Any, context: Any, input_json: Any, tool_display_name: Any
) -> None:
    """Listener for openai_agents.mcp.invoke_tool.before; fails open, only AIGuardAbortError propagates."""
    try:
        if not aiguard_config._ai_guard_mcp_enabled:
            return
        tool_name = getattr(tool, "name", None)
        if not isinstance(tool_name, str) or not tool_name:
            return
        call_id = getattr(context, "tool_call_id", None)
        model_name = tool_display_name or getattr(context, "tool_name", None) or tool_name
        _evaluate_mcp_call(
            client,
            server,
            call_id if isinstance(call_id, str) and call_id else local_tool_call_id(),
            str(model_name),
            input_json if isinstance(input_json, str) and input_json else "{}",
            tool_name,
        )
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("Failed to evaluate openai-agents MCP tool call", exc_info=True)


def _openai_agents_mcp_call_tool_before(client: AIGuardClient, server: Any, tool_name: Any, arguments: Any) -> None:
    """Listener for openai_agents.mcp.call_tool.before (direct calls); fails open, only AIGuardAbortError propagates."""
    try:
        if not aiguard_config._ai_guard_mcp_enabled:
            return
        if not isinstance(tool_name, str) or not tool_name:
            return
        # Direct call: no model asked for it, so an ID marked local.
        _evaluate_mcp_call(
            client, server, local_tool_call_id(), tool_name, json.dumps(arguments or {}, default=str), tool_name
        )
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("Failed to evaluate openai-agents MCP tool call", exc_info=True)
