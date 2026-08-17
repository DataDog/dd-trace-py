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


class ToolCall(TypedDict):
    id: str
    function: Function


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
