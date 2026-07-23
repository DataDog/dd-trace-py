from typing import Any
from typing import Callable
from typing import Optional
from typing import TypedDict
from typing import Union


JSONType = Union[str, int, float, bool, None, list["JSONType"], dict[str, "JSONType"]]
ExperimentConfigType = dict[str, JSONType]


class ExportedLLMObsSpan(TypedDict):
    span_id: str
    trace_id: str
    # True only for spans with OTel gen.ai semantics (e.g. from OTel LLM instrumentations)
    is_otel: bool


class SpanWithTagValue(TypedDict):
    tag_key: str
    tag_value: str
    # True only for spans with OTel gen.ai semantics (e.g. from OTel LLM instrumentations)
    is_otel: bool


class Document(TypedDict, total=False):
    name: str
    id: str
    text: str
    score: float


class ToolCall(TypedDict, total=False):
    name: str
    arguments: dict[str, Any]
    tool_id: str
    type: str


class ToolResult(TypedDict, total=False):
    name: str
    result: str
    tool_id: str
    type: str


class ToolDefinition(TypedDict, total=False):
    name: str
    description: str
    schema: dict[str, Any]
    version: str


class ChatMessage(TypedDict):
    """A single message in a chat prompt template."""

    role: str
    content: str


class PromptResponse(TypedDict, total=False):
    # Mirrors the backend PromptTemplate struct (dd-source domain/prompt.go);
    # not all fields are populated by every CRUD route.
    id: str
    prompt_id: str
    title: str
    description: str
    created_at: str
    source: str
    num_versions: int
    in_registry: bool
    created_from: str
    author: str
    ml_app: str
    ml_apps: list[str]
    last_version_created_at: str
    extracted_from: str


class PromptVersionResponse(TypedDict, total=False):
    id: str
    prompt_uuid: str
    prompt_id: str
    template: Union[str, list[ChatMessage]]
    version: int
    user_version: str
    labels: list[str]
    created_at: str
    version_created_at: str
    author: str
    description: str
    ml_app: str


class DeletedPromptResponse(TypedDict, total=False):
    id: str
    prompt_id: str
    deleted_at: str


class AudioPart(TypedDict, total=False):
    """An audio segment on a Message: inline base64 ``content`` or an offloaded ``attachment_key``."""

    mime_type: str
    content: str
    attachment_key: str


class ImagePart(TypedDict, total=False):
    """An image on a Message: inline base64 ``content`` or an offloaded ``attachment_key``.
     Note: inline ``content`` counts toward the 5 MB per-event size limit. When an event
    exceeds that limit its entire input/output is replaced with a dropped-value placeholder) — there is no image-aware
    truncation yet.
    """

    mime_type: str
    content: str
    attachment_key: str


class Message(TypedDict, total=False):
    id: str
    role: str
    content: str
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    tool_id: str
    audio_parts: list[AudioPart]
    image_parts: list[ImagePart]


class _SpanField(TypedDict):
    kind: str


class _ErrorField(TypedDict, total=False):
    message: str
    stack: str
    type: str


class Prompt(TypedDict, total=False):
    """
    A Prompt object that contains the information needed to render a prompt.
        id: str - the id of the prompt set by the user. Should be unique per ml_app.
        version: str - user tag for the version of the prompt.
        variables: dict[str, str] - a dictionary of variables that will be used to render the prompt
        label: str - label associated with the prompt version (for example, "production")
        chat_template: Optional[Union[list[dict[str, str]], list[Message]]]
            - A list of dicts of (role,template)
            where role is the role of the prompt and template is the template string
        template: Optional[str]
            - It also accepts a string that represents the template for the prompt. Will default to "user" for a role
        tags: Optional[dict[str, str]]
            - list of tags to add to the prompt run.
        rag_context_variables: list[str] - a list of variable key names that contain ground truth context information
        rag_query_variables: list[str] - a list of variable key names that contains query information
        prompt_uuid: str - the uuid of the prompt (set internally by LLMObs.get_prompt)
        prompt_version_uuid: str - the uuid of the prompt version (set internally by LLMObs.get_prompt)
    """

    version: str
    id: str
    label: str
    template: str
    chat_template: Union[list[dict[str, str]], list[Message]]
    variables: dict[str, str]
    tags: dict[str, str]
    rag_context_variables: list[str]
    rag_query_variables: list[str]
    prompt_uuid: str
    prompt_version_uuid: str


class _MetaIO(TypedDict, total=False):
    parameters: dict[str, Any]
    value: str
    messages: list[Message]
    prompt: Prompt
    documents: list[Document]


class _ToolField(TypedDict, total=False):
    version: str


class _Meta(TypedDict, total=False):
    model_name: str
    model_provider: str
    span: _SpanField
    error: _ErrorField
    metadata: dict[str, Any]
    input: _MetaIO
    output: _MetaIO
    expected_output: _MetaIO
    evaluations: Any
    tool: _ToolField
    tool_definitions: list[ToolDefinition]
    intent: str
    agent_attribution: dict[str, Optional[str]]


class _SpanLink(TypedDict):
    span_id: str
    trace_id: str
    attributes: dict[str, str]


PromptFallback = Optional[Union[str, list[Message], Prompt, Callable[[], Union[str, list[Message], Prompt]]]]


class PromptAPIError(Exception):
    """Base exception for prompt management API errors."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Prompt API error ({status}): {detail}")


class PromptAuthError(PromptAPIError):
    """Raised on 401 Unauthorized or 403 Forbidden."""

    pass


class PromptValidationError(PromptAPIError):
    """Raised on 400 Bad Request."""

    pass


class PromptNotFoundError(PromptAPIError):
    """Raised on 404 Not Found."""

    pass


class PromptConflictError(PromptAPIError):
    """Raised on 409 Conflict."""

    pass


class PromptServerError(PromptAPIError):
    """Raised on 5xx server errors."""

    pass
