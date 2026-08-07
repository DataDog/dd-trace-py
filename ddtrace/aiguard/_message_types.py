"""Canonical AI Guard message shapes.

Split out of `_api_client` so that modules needing only the message types (e.g.
`_redaction`, which redacts fields on a `Message` list) do not have to import the whole
client stack (HTTP transport, telemetry, tracer) just for a type hint.
"""

from typing import Optional  # noqa:F401
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
