from typing import Any
from typing import TypedDict
from typing import Union


JSONType = Union[str, int, float, bool, None, list["JSONType"], dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, list[JSONType], dict[str, JSONType]]
ExperimentConfigType = dict[str, JSONType]
DatasetRecordInputType = dict[str, NonNoneJSONType]


class ExportedLLMObsSpan(TypedDict):
    span_id: str
    trace_id: str


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


class Message(TypedDict, total=False):
    id: str
    role: str
    content: str
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    tool_id: str


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
        chat_template: Optional[Union[list[dict[str, str]], list[Message]]]
            - A list of dicts of (role,template)
            where role is the role of the prompt and template is the template string
        template: Optional[str]
            - It also accepts a string that represents the template for the prompt. Will default to "user" for a role
        tags: Optional[dict[str, str]]
            - list of tags to add to the prompt run.
        rag_context_variables: list[str] - a list of variable key names that contain ground truth context information
        rag_query_variables: list[str] - a list of variable key names that contains query information
    """

    version: str
    id: str
    template: str
    chat_template: Union[list[dict[str, str]], list[Message]]
    variables: dict[str, str]
    tags: dict[str, str]
    rag_context_variables: list[str]
    rag_query_variables: list[str]


class _MetaIO(TypedDict, total=False):
    parameters: dict[str, Any]
    value: str
    messages: list[Message]
    prompt: Prompt
    documents: list[Document]


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
    tool_definitions: list[ToolDefinition]
    intent: str


class _SpanLink(TypedDict):
    span_id: str
    trace_id: str
    attributes: dict[str, str]
