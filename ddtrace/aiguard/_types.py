"""Shared message types for AI Guard evaluation and redaction.

Split out from ``_api_client`` so that ``_redaction`` can depend on ``Message`` without
creating an import cycle back into ``_api_client``.
"""

from typing import Optional
from typing import TypedDict
from typing import Union


class Function(TypedDict):
    name: str
    arguments: str


class _MCPRequired(TypedDict):
    transport: str
    tool_name: str


class MCP(_MCPRequired, total=False):
    """MCP provenance of a tool call.

    transport is one of streamable_http, sse, stdio, in_process or unknown, and tool_name the
    original server-side name. name is the configured server label and url the sanitized remote
    endpoint; both are omitted when unknown, never guessed.
    """

    name: str
    url: str


class _ToolCallRequired(TypedDict):
    id: str
    function: Function


class ToolCall(_ToolCallRequired, total=False):
    # function.name stays the model-visible name; mcp.tool_name resolves adapter prefixes.
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
