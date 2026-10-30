"""Shared message types for AI Guard evaluation and redaction.

Split out from ``_api_client`` so that ``_redaction`` can depend on ``Message`` without
creating an import cycle back into ``_api_client``.
"""

from typing import Any
from typing import Literal
from typing import Optional
from typing import TypedDict
from typing import Union


class Function(TypedDict):
    name: str
    arguments: str


class MCP(TypedDict):
    """MCP reference of a tool call: server_id matches an mcp_servers entry of the same request."""

    server_id: str


MCPManagedBy = Literal["client", "provider"]
MCPTransport = Literal["streamable_http", "sse", "stdio", "in_process", "unknown"]


class _MCPServerRequired(TypedDict):
    id: str
    managed_by: MCPManagedBy
    transport: MCPTransport
    tools: dict[str, str]


class MCPServer(_MCPServerRequired, total=False):
    """MCP server referenced by tool calls; tools maps function.name to the original MCP tool name.

    Unknown name, url, command and args are omitted; url is only scheme://host[:port].
    """

    name: str
    url: str
    command: str
    args: list[str]


class _ToolCallRequired(TypedDict):
    id: str
    function: Function


class ToolCall(_ToolCallRequired, total=False):
    mcp: MCP


class ImageURL(TypedDict, total=False):
    url: str


class ContentPart(TypedDict, total=False):
    type: str
    text: Optional[str]
    image_url: Optional[ImageURL]


class Message(TypedDict, total=False):
    role: str
    content: Union[str, list[ContentPart]]
    tool_call_id: str
    tool_calls: list[ToolCall]


class Evaluation(TypedDict):
    action: Literal["ALLOW", "DENY", "ABORT"]
    reason: str
    tags: list[str]
    sds: list[Any]
    tag_probs: dict[str, float]
    # The evaluated conversation, redacted when the AI Guard service asked for it and otherwise
    # the very same list passed to evaluate. It can carry sensitive data: never log it.
    messages: list[Message]
