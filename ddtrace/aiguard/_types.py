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


class Evaluation(TypedDict):
    action: Literal["ALLOW", "DENY", "ABORT"]
    reason: str
    tags: list[str]
    sds: list[Any]
    tag_probs: dict[str, float]
    # The evaluated conversation, redacted when the AI Guard service asked for it and otherwise
    # the very same list passed to evaluate. It can carry sensitive data: never log it.
    messages: list[Message]
