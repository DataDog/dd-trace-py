from typing import Any
from typing import Dict
from typing import List
from typing import Optional
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
Message = TypedDict("Message", {"content": str, "role": str}, total=False)
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

    version: Optional[str]
    id: Optional[str]
    template: Optional[str]
    chat_template: Optional[Union[List[Dict[str, str]], List[Message]]]
    variables: Optional[Dict[str, str]]
    tags: Optional[Dict[str, str]]
    rag_context_variables: Optional[List[str]]
    rag_query_variables: Optional[List[str]]


class Messages:
    def __init__(self, messages: Union[List[Dict[str, str]], Dict[str, str], str]):
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
            if not role:
                self.messages.append(Message(content=content))
                continue
            if not isinstance(role, str):
                raise TypeError("Message role must be a string, and one of .")
            self.messages.append(Message(content=content, role=role))


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
