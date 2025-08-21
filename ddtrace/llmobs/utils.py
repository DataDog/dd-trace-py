from typing import Any
from typing import Dict
from typing import List
from typing import Union


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    from typing_extensions import TypedDict

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

DocumentType = Dict[str, Union[str, int, float]]

ExportedLLMObsSpan = TypedDict("ExportedLLMObsSpan", {"span_id": str, "trace_id": str})
Document = TypedDict("Document", {"name": str, "id": str, "text": str, "score": float}, total=False)
Message = TypedDict(
    "Message",
    {
        "content": str,
        "role": str,
        "tool_calls": List["ToolCall"],
        "tool_results": List["ToolResult"],
        "tool_definitions": List["ToolDefinition"],
    },
    total=False,
)
Prompt = TypedDict(
    "Prompt",
    {
        "variables": Dict[str, str],
        "template": str,
        "id": str,
        "version": str,
        "rag_context_variables": List[
            str
        ],  # a list of variable key names that contain ground truth context information
        "rag_query_variables": List[str],  # a list of variable key names that contains query information
    },
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
                formatted_tool_calls = []
                for tool_call in tool_calls:
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

                    formatted_tool_calls.append(formatted_tool_call)

                msg_dict["tool_calls"] = formatted_tool_calls

            tool_results = message.get("tool_results")
            if tool_results is not None:
                if not isinstance(tool_results, list):
                    raise TypeError("tool_results must be a list.")
                formatted_tool_results = []
                for tool_result in tool_results:
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

                    formatted_tool_results.append(formatted_tool_result)

                msg_dict["tool_results"] = formatted_tool_results

            tool_definitions = message.get("tool_definitions")
            if tool_definitions is not None:
                if not isinstance(tool_definitions, list):
                    raise TypeError("tool_definitions must be a list.")
                formatted_tool_definitions = []
                for tool_definition in tool_definitions:
                    if not isinstance(tool_definition, dict):
                        raise TypeError("Each tool_definition must be a dictionary.")

                    # name is required
                    name = tool_definition.get("name")
                    if not name or not isinstance(name, str):
                        raise TypeError("ToolDefinition name must be a non-empty string.")

                    formatted_tool_definition = ToolDefinition(name=name)

                    # Add optional fields if present
                    description = tool_definition.get("description")
                    if description and isinstance(description, str):
                        formatted_tool_definition["description"] = description

                    schema = tool_definition.get("schema")
                    if schema and isinstance(schema, dict):
                        formatted_tool_definition["schema"] = schema

                    formatted_tool_definitions.append(formatted_tool_definition)

                msg_dict["tool_definitions"] = formatted_tool_definitions

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
