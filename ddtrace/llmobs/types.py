from typing import Any
from typing import Dict
from typing import List
from typing import TypedDict
from typing import Union


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
    arguments: Dict[str, Any]
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
    schema: Dict[str, Any]


class Message(TypedDict, total=False):
    id: str
    role: str
    content: str
    tool_calls: List[ToolCall]
    tool_results: List[ToolResult]
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
        variables: Dict[str, str] - a dictionary of variables that will be used to render the prompt
        chat_template: Optional[Union[List[Dict[str, str]], List[Message]]]
            - A list of dicts of (role,template)
            where role is the role of the prompt and template is the template string
        template: Optional[str]
            - It also accepts a string that represents the template for the prompt. Will default to "user" for a role
        tags: Optional[Dict[str, str]]
            - List of tags to add to the prompt run.
        rag_context_variables: List[str] - a list of variable key names that contain ground truth context information
        rag_query_variables: List[str] - a list of variable key names that contains query information
    """

    version: str
    id: str
    template: str
    chat_template: Union[List[Dict[str, str]], List[Message]]
    variables: Dict[str, str]
    tags: Dict[str, str]
    rag_context_variables: List[str]
    rag_query_variables: List[str]


class _MetaIO(TypedDict, total=False):
    parameters: Dict[str, Any]
    value: str
    messages: List[Message]
    prompt: Prompt
    documents: List[Document]


class _Meta(TypedDict, total=False):
    model_name: str
    model_provider: str
    span: _SpanField
    error: _ErrorField
    metadata: Dict[str, Any]
    input: _MetaIO
    output: _MetaIO
    expected_output: _MetaIO
    evaluations: Any
    tool_definitions: List[ToolDefinition]


class _SpanLink(TypedDict):
    span_id: str
    trace_id: str
    attributes: Dict[str, str]
