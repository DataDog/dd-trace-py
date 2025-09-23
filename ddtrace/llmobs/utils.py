from typing import Any
from typing import Dict
from typing import List
from typing import TypedDict  # noqa:F401
from typing import Union

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

DocumentType = Dict[str, Union[str, int, float]]


def _extract_tool_call(tool_call: Dict[str, Any]) -> "ToolCall":
    """Extract and validate a tool call dictionary."""
    if not isinstance(tool_call, dict):
        raise TypeError("Each tool_call must be a dictionary.")

    # name and arguments are required
    name = tool_call.get("name")
    arguments = tool_call.get("arguments")

    if not name or not isinstance(name, str):
        raise TypeError("ToolCall name must be a non-empty string.")
    if arguments is None or not isinstance(arguments, dict):
        raise TypeError("ToolCall arguments must be a dictionary.")

    formatted_tool_call = ToolCall(name=name, arguments=arguments)

    # Add optional fields if present
    tool_id = tool_call.get("tool_id")
    if tool_id and isinstance(tool_id, str):
        formatted_tool_call["tool_id"] = tool_id

    tool_type = tool_call.get("type")
    if tool_type and isinstance(tool_type, str):
        formatted_tool_call["type"] = tool_type

    return formatted_tool_call


def _extract_tool_result(tool_result: Dict[str, Any]) -> "ToolResult":
    """Extract and validate a tool result dictionary."""
    if not isinstance(tool_result, dict):
        raise TypeError("Each tool_result must be a dictionary.")

    # result is required
    result = tool_result.get("result")
    if result is None or not isinstance(result, str):
        raise TypeError("ToolResult result must be a string.")

    formatted_tool_result = ToolResult(result=result)

    # Add optional fields if present
    name = tool_result.get("name")
    if name and isinstance(name, str):
        formatted_tool_result["name"] = name

    tool_id = tool_result.get("tool_id")
    if tool_id and isinstance(tool_id, str):
        formatted_tool_result["tool_id"] = tool_id

    tool_type = tool_result.get("type")
    if tool_type and isinstance(tool_type, str):
        formatted_tool_result["type"] = tool_type

    return formatted_tool_result


ExportedLLMObsSpan = TypedDict("ExportedLLMObsSpan", {"span_id": str, "trace_id": str})
Document = TypedDict("Document", {"name": str, "id": str, "text": str, "score": float}, total=False)
Message = TypedDict(
    "Message",
    {"content": str, "role": str, "tool_calls": List["ToolCall"], "tool_results": List["ToolResult"]},
    total=False,
)
ToolCall = TypedDict(
    "ToolCall",
    {
        "name": str,
        "arguments": Dict[str, Any],
        "tool_id": str,
        "type": str,
    },
    total=False,
)
ToolResult = TypedDict(
    "ToolResult",
    {
        "name": str,
        "result": str,
        "tool_id": str,
        "type": str,
    },
    total=False,
)
ToolDefinition = TypedDict(
    "ToolDefinition",
    {
        "name": str,
        "description": str,
        "schema": Dict[str, Any],
    },
    total=False,
)


def extract_tool_definitions(tool_definitions: List[Dict[str, Any]]) -> List[ToolDefinition]:
    """Return a list of validated tool definitions."""
    if not isinstance(tool_definitions, list):
        log.warning("tool_definitions must be a list of dictionaries.")
        return []

    validated_tool_definitions = []
    for i, tool_def in enumerate(tool_definitions):
        if not isinstance(tool_def, dict):
            log.warning("Tool definition at index %s must be a dictionary. Skipping.", i)
            continue

        # name is required
        name = tool_def.get("name")
        if not name or not isinstance(name, str):
            log.warning("Tool definition at index %s must have a non-empty 'name' field. Skipping.", i)
            continue

        validated_tool_def = ToolDefinition(name=name)

        # description is optional
        description = tool_def.get("description")
        if description is not None:
            if not isinstance(description, str):
                log.warning(
                    "Tool definition 'description' at index %s must be a string. Skipping description field.", i
                )
            else:
                validated_tool_def["description"] = description

        # schema is optional
        schema = tool_def.get("schema")
        if schema is not None:
            if not isinstance(schema, dict):
                log.warning("Tool definition 'schema' at index %s must be a dictionary. Skipping schema field.", i)
            else:
                validated_tool_def["schema"] = schema

        validated_tool_definitions.append(validated_tool_def)

    return validated_tool_definitions


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


class Messages:
    def __init__(self, messages: Union[List[Dict[str, Any]], Dict[str, Any], str]):
        self.messages = []
        if not isinstance(messages, list):
            messages = [messages]  # type: ignore[list-item]
        for message in messages:
            if isinstance(message, str):
                self.messages.append(Message(content=message))
                continue
            elif not isinstance(message, dict):
                raise TypeError("messages must be a string, dictionary, or list of dictionaries.")

            content = message.get("content", "")
            role = message.get("role")
            if not isinstance(content, str):
                raise TypeError("Message content must be a string.")

            msg_dict = Message(content=content)
            if role:
                if not isinstance(role, str):
                    raise TypeError("Message role must be a string.")
                msg_dict["role"] = role

            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                if not isinstance(tool_calls, list):
                    raise TypeError("tool_calls must be a list.")
                formatted_tool_calls = [_extract_tool_call(tool_call) for tool_call in tool_calls]
                msg_dict["tool_calls"] = formatted_tool_calls

            tool_results = message.get("tool_results")
            if tool_results is not None:
                if not isinstance(tool_results, list):
                    raise TypeError("tool_results must be a list.")
                formatted_tool_results = [_extract_tool_result(tool_result) for tool_result in tool_results]
                msg_dict["tool_results"] = formatted_tool_results

            self.messages.append(msg_dict)


class Documents:
    def __init__(self, documents: Union[List[DocumentType], DocumentType, str]):
        self.documents = []
        if not isinstance(documents, list):
            documents = [documents]  # type: ignore[list-item]
        for document in documents:
            if isinstance(document, str):
                self.documents.append(Document(text=document))
                continue
            elif not isinstance(document, dict):
                raise TypeError("documents must be a string, dictionary, or list of dictionaries.")
            document_text = document.get("text")
            document_name = document.get("name")
            document_id = document.get("id")
            document_score = document.get("score")
            if not isinstance(document_text, str):
                raise TypeError("Document text must be a string.")
            formatted_document = Document(text=document_text)
            if document_name:
                if not isinstance(document_name, str):
                    raise TypeError("document name must be a string.")
                formatted_document["name"] = document_name
            if document_id:
                if not isinstance(document_id, str):
                    raise TypeError("document id must be a string.")
                formatted_document["id"] = document_id
            if document_score:
                if not isinstance(document_score, (int, float)):
                    raise TypeError("document score must be an integer or float.")
                formatted_document["score"] = document_score
            self.documents.append(formatted_document)
