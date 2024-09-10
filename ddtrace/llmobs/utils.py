from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    from typing_extensions import TypedDict

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

DocumentType = Dict[str, Union[str, int, float]]


class ToolCall(BaseModel):
    name: Optional[str] = ""
    arguments: Optional[Dict] = {}
    tool_id: Optional[str] = ""
    type: Optional[str] = ""


class Prompt(BaseModel):
    template: str = ""
    variables: Dict = {}
    prompt_version: str = ""


class MetaIO(BaseModel):
    prompt: Optional[Prompt] = None
    value: Optional[str] = None
    # (TODO(<owner>)): lievan, let Messages and Documents inherit from BaseModel
    documents: Optional[List[DocumentType]] = None
    messages: Optional[List[Dict[str, str]]] = None


class SpanField(BaseModel):
    kind: str = ""


class Error(BaseModel):
    message: str = ""
    stack: str = ""


class Meta(BaseModel):
    # model_* is a protected namespace in pydantic, so we need to add this line to allow
    # for model_* fields
    model_config = ConfigDict(protected_namespaces=())
    span: SpanField = SpanField()
    error: Error = Error()
    input: MetaIO = MetaIO()
    output: MetaIO = MetaIO()
    metadata: Dict = {}
    # (TODO(<owner>)) lievan: validate model_* fields are only present on certain span types
    model_name: str = ""
    model_provider: str = ""


class LLMObsSpanContext(BaseModel):
    span_id: str
    trace_id: str
    name: str
    ml_app: str
    meta: Meta = Meta()
    session_id: str = ""
    metrics: Dict[str, Union[int, float]] = {}
    tags: List[str] = []


class EvaluationMetric(BaseModel):
    label: str
    categorical_value: str = ""
    score_value: Union[int, float] = 0.0
    metric_type: str
    tags: List[str] = []
    ml_app: str
    timestamp_ms: int
    span_id: str
    trace_id: str


ExportedLLMObsSpan = TypedDict("ExportedLLMObsSpan", {"span_id": str, "trace_id": str})
Document = TypedDict("Document", {"name": str, "id": str, "text": str, "score": float}, total=False)
Message = TypedDict("Message", {"content": str, "role": str}, total=False)


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
            self.messages.append(
                {
                    "content": content,
                    "role": role,
                }
            )


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
