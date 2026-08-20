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


class _FeedbackSubmitterOptional(TypedDict, total=False):
    type: str


class FeedbackSubmitter(_FeedbackSubmitterOptional):
    id: str


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


class AgentCapability(TypedDict, total=False):
    """One declared capability: an MCP server, a builtin tool, a toolset, or a preparation hook."""

    name: str
    type: str


class AgentInstructionResolver(TypedDict, total=False):
    """A callable that decides instruction text at run time, recorded by name and never evaluated."""

    name: str
    type: str


class AgentTool(TypedDict, total=False):
    """One tool an agent declares it can call.

    parameters maps a name to ``{"type": ..., "required": True}``. An optional parameter omits
    ``required`` rather than reporting it false, which is the shape the framework integrations
    already emit, so a hand-declared tool renders the same as an auto-instrumented one.
    """

    name: str
    description: str
    parameters: dict[str, Any]


class AgentManifest(TypedDict, total=False):
    """Declared agent configuration, reported on an agent span under _dd.agent_manifest.

    One flat document. Every key is optional because a field the framework does not expose is
    omitted rather than emitted empty, so an absent key means "not configured". Only declared
    configuration is read, never what a single run resolved, so the document is stable run to run.
    """

    framework: str
    name: str
    instructions: str
    system_prompts: list[str]
    extra_instructions: list[AgentInstructionResolver]
    model: str
    model_settings: dict[str, Any]
    agent_settings: dict[str, Any]
    tools: list[dict[str, Any]]
    capabilities: list[AgentCapability]
    data_contracts: dict[str, Any]
    guardrails: list[str]
    handoffs: list[Any]
    handoff_description: str
    memory_policies: list[str]
    metadata: dict[str, Any]


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

    Note: inline ``content`` counts toward the 5 MB per-event size limit; when an event exceeds it the
    whole input/output is replaced with a dropped-value placeholder. Integrations therefore cap the size
    of a single inline image they capture and keep a text marker instead -- but several images that each
    fit can still collectively exceed the limit, as there is no image-aware truncation in the writer yet.
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


class Agent(TypedDict, total=False):
    """
    An Agent object that declares the agent an agent span represents.
        version: str - user tag for the version of the agent.
        name: str - overrides the agent's name, which defaults to the agent span's name.
        instructions: str - the system instructions the agent runs with.
        model: str - the model the agent is configured to call.
        model_settings: dict[str, Any] - inference parameters such as temperature or max_tokens.
            Filtered by ALLOWED_MODEL_SETTINGS_KEYS: generic inference parameters are reported and
            provider-specific ones such as extra_headers are dropped, since they can carry secrets.
        tools: list[AgentTool] - the tools the agent declares it can call.

    `version` becomes an `agent_version` tag and the rest the agent's manifest, on the agent span
    only, never on its children. Unreportable values are dropped rather than raising. Keys are
    merged into a manifest an integration already reported, so annotating one field leaves the rest.
    """

    version: str
    name: str
    instructions: str
    model: str
    model_settings: dict[str, Any]
    tools: list[AgentTool]


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
