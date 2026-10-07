"""AI Guard evaluation of MCP tool calls run by openai-agents MCP servers.

openai-agents converts the tools of each configured MCP server into function tools and runs the
ones the model selects through MCPUtil.invoke_mcp_tool, which knows the server, the original tool
name, the model-visible name and the model call ID. Applications can also call server.call_tool
directly. Both are evaluated before the tools/call request is sent; an agent-driven call is
evaluated once, at the adapter, and the lower server.call_tool check is skipped for it.
"""

from collections.abc import Mapping
from contextvars import ContextVar
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
from ddtrace.aiguard._types import MCP
from ddtrace.aiguard.integrations._mcp import local_tool_call_id
from ddtrace.aiguard.integrations._mcp import mcp_metadata
from ddtrace.aiguard.integrations._mcp import tool_call_conversation
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)

# Matched on the class hierarchy so subclasses of the SDK servers keep their transport.
_TRANSPORT_BY_SERVER_CLASS = {
    "MCPServerStdio": AI_GUARD.MCP_TRANSPORT_STDIO,
    "MCPServerSse": AI_GUARD.MCP_TRANSPORT_SSE,
    "MCPServerStreamableHttp": AI_GUARD.MCP_TRANSPORT_STREAMABLE_HTTP,
}
# Names the SDK generates when none is configured. They embed the stdio command or the raw URL,
# so they are never reported.
_GENERATED_NAME_PREFIXES = ("stdio: ", "sse: ", "streamable_http: ")


class _AdapterCall:
    """An agent-driven call already evaluated at the adapter, consumed by its server.call_tool."""

    __slots__ = ("server", "tool_name", "consumed")

    def __init__(self, server: Any, tool_name: str) -> None:
        self.server = server
        self.tool_name = tool_name
        self.consumed = False


_adapter_call: ContextVar[Optional[_AdapterCall]] = ContextVar("ai_guard_openai_agents_mcp_call", default=None)


def _server_mcp(server: Any, tool_name: str) -> MCP:
    transport = next(
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
    url = None
    if transport in (AI_GUARD.MCP_TRANSPORT_SSE, AI_GUARD.MCP_TRANSPORT_STREAMABLE_HTTP):
        params = getattr(server, "params", None)
        candidate = params.get("url") if isinstance(params, Mapping) else None
        url = candidate if isinstance(candidate, str) else None
    return mcp_metadata(transport, tool_name, name=name, url=url)


def _evaluate(client: AIGuardClient, messages: list[Message]) -> None:
    try:
        evaluate_auto(client, messages, AI_GUARD.INTEGRATION_OPENAI_AGENTS)
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("Failed to evaluate openai-agents MCP tool call", exc_info=True)


def _openai_agents_mcp_invoke_tool_before(
    client: AIGuardClient, server: Any, tool: Any, context: Any, input_json: Any, tool_display_name: Any
) -> None:
    """Listener for openai_agents.mcp.invoke_tool.before."""
    tool_name = getattr(tool, "name", None)
    if not isinstance(tool_name, str) or not tool_name:
        return
    call_id = getattr(context, "tool_call_id", None)
    model_name = tool_display_name or getattr(context, "tool_name", None) or tool_name
    tool_call = ToolCall(
        id=call_id if isinstance(call_id, str) and call_id else local_tool_call_id(),
        function=Function(
            name=str(model_name), arguments=input_json if isinstance(input_json, str) and input_json else "{}"
        ),
        mcp=_server_mcp(server, tool_name),
    )
    _evaluate(client, tool_call_conversation(tool_call))
    # Set only once the call may proceed, so a blocked call cannot excuse a later direct call.
    _adapter_call.set(_AdapterCall(server, tool_name))


def _openai_agents_mcp_call_tool_before(client: AIGuardClient, server: Any, tool_name: Any, arguments: Any) -> None:
    """Listener for openai_agents.mcp.call_tool.before."""
    if not isinstance(tool_name, str) or not tool_name:
        return
    adapter_call = _adapter_call.get()
    if (
        adapter_call is not None
        and not adapter_call.consumed
        and adapter_call.server is server
        and adapter_call.tool_name == tool_name
    ):
        adapter_call.consumed = True
        return
    # Direct call: no model asked for it, so no history and an ID marked local.
    tool_call = ToolCall(
        id=local_tool_call_id(),
        function=Function(name=tool_name, arguments=json.dumps(arguments or {}, default=str)),
        mcp=_server_mcp(server, tool_name),
    )
    _evaluate(client, [Message(role="assistant", tool_calls=[tool_call])])
