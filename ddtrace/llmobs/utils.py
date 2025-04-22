from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.llmobs._constants import SPAN_LINKS


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    from typing_extensions import TypedDict

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.trace import Span


log = get_logger(__name__)

DocumentType = Dict[str, Union[str, int, float]]

ExportedLLMObsSpan = TypedDict("ExportedLLMObsSpan", {"span_id": str, "trace_id": str})
Document = TypedDict("Document", {"name": str, "id": str, "text": str, "score": float}, total=False)
Message = TypedDict("Message", {"content": str, "role": str}, total=False)
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


class LLMObsState(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.llmobs_service = kwargs.get("llmobs_service", None)
        self.proxy: Dict[str, Dict[str, Any]] = kwargs.get("_proxy", {})

        self.reading = False

    def set_reading(self, carrier: Optional[Dict[str, Any]] = None, carrier_key: Optional[str] = None):
        self.carrier = carrier
        self.carrier_key = carrier_key

        self.reading = True

    def stop_reading(self):
        self.reading = False

        self.carrier = None
        self.carrier_key = None

    def __getitem__(self, key):
        self._handle_get(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._handle_get(key)
        return super().get(key, default)

    def _handle_get(self, key: str):
        if not self.reading:
            return

        from_spans_meta: Optional[Dict[str, Any]] = self.proxy.get(key, None)

        current_span: Span = self.llmobs_service._current_span()
        existing_links = (
            self.carrier.get(self.carrier_key, [])
            if self.carrier is not None and self.carrier_key is not None
            else current_span._get_ctx_item(SPAN_LINKS) or []
        )

        if not from_spans_meta:
            return

        from_spans: Optional[List[Dict[str, Any]]] = from_spans_meta.get("spans", None)
        if from_spans is None:
            return

        for span in from_spans:
            if span is None:
                continue
            existing_links.append(
                {
                    "trace_id": span["trace_id"],
                    "span_id": span["span_id"],
                    "attributes": {
                        "source": "influence",
                        "accessed_attribute": key,
                    },
                }
            )

        from_spans_meta["used"] = True

        if self.carrier is not None and self.carrier_key is not None:
            self.carrier[self.carrier_key] = existing_links
        else:
            current_span._set_ctx_item(SPAN_LINKS, existing_links)

    def _handle_set(self, key: str):
        if key in ("_proxy", "llmobs_service"):
            return

        current_span: Span = self.llmobs_service._current_span()
        spans_meta: Dict[str, Any] = self.proxy.setdefault(key, {})
        spans: Optional[List[Dict[str, Any]]] = spans_meta.get("spans", None)
        if spans is None:
            spans_meta["spans"] = [
                {
                    "trace_id": format_trace_id(current_span.trace_id),
                    "span_id": str(current_span.span_id),
                }
            ]
        else:
            if spans_meta.get("used", False):
                spans.clear()
                del spans_meta["used"]
            spans.append(
                {
                    "trace_id": format_trace_id(current_span.trace_id),
                    "span_id": str(current_span.span_id),
                }
            )

    def to_state_dict(self):
        dict_keys = [key for key in self.keys() if key not in ("_proxy", "llmobs_service")]
        return {key: self[key] for key in dict_keys}

    @staticmethod
    def from_state(llmobs_states: Union["LLMObsState", List["LLMObsState"]], state: Dict, service):
        llmobs_proxies = llmobs_states if isinstance(llmobs_states, list) else [llmobs_states]

        # merge spans of all llmobs_states
        merged_llmobs_proxies: Dict[str, Dict[str, Any]] = {}
        for llmobs_proxy in llmobs_proxies:
            if llmobs_proxy is None:
                continue
            for key, value in llmobs_proxy.proxy.items():
                if key not in merged_llmobs_proxies:
                    merged_llmobs_proxies[key] = value
                else:
                    merged_llmobs_proxies[key]["spans"].extend(value["spans"])

        return LLMObsState(state, _proxy=merged_llmobs_proxies, llmobs_service=service)

    @staticmethod
    def from_dict(state: Optional[Dict[str, Any]]):
        if isinstance(state, LLMObsState) or state is None:
            return state

        proxy = state.pop("_proxy", {})
        llmobs_service = state.pop("llmobs_service", None)
        return LLMObsState(state, _proxy=proxy, llmobs_service=llmobs_service)
