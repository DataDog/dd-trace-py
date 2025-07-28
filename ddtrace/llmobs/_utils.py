from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import is_dataclass
import json
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import CREWAI_APM_SPAN_NAME
from ddtrace.llmobs._constants import GEMINI_APM_SPAN_NAME
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS
from ddtrace.llmobs._constants import IS_EVALUATION_SPAN
from ddtrace.llmobs._constants import LANGCHAIN_APM_SPAN_NAME
from ddtrace.llmobs._constants import LITELLM_APM_SPAN_NAME
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OPENAI_APM_SPAN_NAME
from ddtrace.llmobs._constants import PROPAGATED_ML_APP_KEY
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._constants import VERTEXAI_APM_SPAN_NAME
from ddtrace.trace import Span


log = get_logger(__name__)


STANDARD_INTEGRATION_SPAN_NAMES = (
    CREWAI_APM_SPAN_NAME,
    GEMINI_APM_SPAN_NAME,
    LANGCHAIN_APM_SPAN_NAME,
    VERTEXAI_APM_SPAN_NAME,
    LITELLM_APM_SPAN_NAME,
)


def validate_prompt(prompt: dict) -> Dict[str, Union[str, dict, List[str]]]:
    validated_prompt = {}  # type: Dict[str, Union[str, dict, List[str]]]
    if not isinstance(prompt, dict):
        raise TypeError("Prompt must be a dictionary")
    variables = prompt.get("variables")
    template = prompt.get("template")
    version = prompt.get("version")
    prompt_id = prompt.get("id")
    ctx_variable_keys = prompt.get("rag_context_variables")
    rag_query_variable_keys = prompt.get("rag_query_variables")
    if variables is not None:
        if not isinstance(variables, dict):
            raise TypeError("Prompt variables must be a dictionary.")
        if not any(isinstance(k, str) or isinstance(v, str) for k, v in variables.items()):
            raise TypeError("Prompt variable keys and values must be strings.")
        validated_prompt["variables"] = variables
    if template is not None:
        if not isinstance(template, str):
            raise TypeError("Prompt template must be a string")
        validated_prompt["template"] = template
    if version is not None:
        if not isinstance(version, str):
            raise TypeError("Prompt version must be a string.")
        validated_prompt["version"] = version
    if prompt_id is not None:
        if not isinstance(prompt_id, str):
            raise TypeError("Prompt id must be a string.")
        validated_prompt["id"] = prompt_id
    if ctx_variable_keys is not None:
        if not isinstance(ctx_variable_keys, list):
            raise TypeError("Prompt field `context_variable_keys` must be a list of strings.")
        if not all(isinstance(k, str) for k in ctx_variable_keys):
            raise TypeError("Prompt field `context_variable_keys` must be a list of strings.")
        validated_prompt[INTERNAL_CONTEXT_VARIABLE_KEYS] = ctx_variable_keys
    else:
        validated_prompt[INTERNAL_CONTEXT_VARIABLE_KEYS] = ["context"]
    if rag_query_variable_keys is not None:
        if not isinstance(rag_query_variable_keys, list):
            raise TypeError("Prompt field `rag_query_variables` must be a list of strings.")
        if not all(isinstance(k, str) for k in rag_query_variable_keys):
            raise TypeError("Prompt field `rag_query_variables` must be a list of strings.")
        validated_prompt[INTERNAL_QUERY_VARIABLE_KEYS] = rag_query_variable_keys
    else:
        validated_prompt[INTERNAL_QUERY_VARIABLE_KEYS] = ["question"]
    return validated_prompt


class LinkTracker:
    def __init__(self, object_span_links=None):
        self._object_span_links = object_span_links or {}

    def get_object_id(self, obj):
        return f"{type(obj).__name__}_{id(obj)}"

    def add_span_links_to_object(self, obj, span_links):
        obj_id = self.get_object_id(obj)
        if obj_id not in self._object_span_links:
            self._object_span_links[obj_id] = []
        self._object_span_links[obj_id] += span_links

    def get_span_links_from_object(self, obj):
        return self._object_span_links.get(self.get_object_id(obj), [])


class AnnotationContext:
    def __init__(self, _register_annotator, _deregister_annotator):
        self._register_annotator = _register_annotator
        self._deregister_annotator = _deregister_annotator

    def __enter__(self):
        self._register_annotator()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._deregister_annotator()

    async def __aenter__(self):
        self._register_annotator()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._deregister_annotator()


def _get_attr(o: object, attr: str, default: object):
    # Convenience method to get an attribute from an object or dict
    if isinstance(o, dict):
        return o.get(attr, default)
    return getattr(o, attr, default)


def _get_nearest_llmobs_ancestor(span: Span) -> Optional[Span]:
    """Return the nearest LLMObs-type ancestor span of a given span."""
    parent = span._parent
    while parent:
        if parent.span_type == SpanTypes.LLM:
            return parent
        parent = parent._parent
    return None


def _get_span_name(span: Span) -> str:
    if span.name in STANDARD_INTEGRATION_SPAN_NAMES and span.resource != "":
        return span.resource
    elif span.name == OPENAI_APM_SPAN_NAME and span.resource != "":
        client_name = span.get_tag("openai.request.provider") or "OpenAI"
        return "{}.{}".format(client_name, span.resource)
    return span._get_ctx_item(NAME) or span.name


def _is_evaluation_span(span: Span) -> bool:
    """
    Return whether or not a span is an evaluation span by checking the span's
    nearest LLMObs span ancestor. Default to 'False'
    """
    is_evaluation_span = span._get_ctx_item(IS_EVALUATION_SPAN)
    if is_evaluation_span:
        return is_evaluation_span
    llmobs_parent = _get_nearest_llmobs_ancestor(span)
    while llmobs_parent:
        is_evaluation_span = llmobs_parent._get_ctx_item(IS_EVALUATION_SPAN)
        if is_evaluation_span:
            return is_evaluation_span
        llmobs_parent = _get_nearest_llmobs_ancestor(llmobs_parent)
    return False


def _get_ml_app(span: Span) -> Optional[str]:
    """
    Return the ML app name for a given span, by checking the span's nearest LLMObs span ancestor.
    Default to the global config LLMObs ML app name otherwise.
    """
    ml_app = span._get_ctx_item(ML_APP)
    if ml_app:
        return ml_app
    llmobs_parent = _get_nearest_llmobs_ancestor(span)
    while llmobs_parent:
        ml_app = llmobs_parent._get_ctx_item(ML_APP)
        if ml_app is not None:
            return ml_app
        llmobs_parent = _get_nearest_llmobs_ancestor(llmobs_parent)
    return ml_app or span.context._meta.get(PROPAGATED_ML_APP_KEY) or config._llmobs_ml_app


def _get_session_id(span: Span) -> Optional[str]:
    """Return the session ID for a given span, by checking the span's nearest LLMObs span ancestor."""
    session_id = span._get_ctx_item(SESSION_ID)
    if session_id:
        return session_id
    llmobs_parent = _get_nearest_llmobs_ancestor(span)
    while llmobs_parent:
        session_id = llmobs_parent._get_ctx_item(SESSION_ID)
        if session_id is not None:
            return session_id
        llmobs_parent = _get_nearest_llmobs_ancestor(llmobs_parent)
    return session_id


def _unserializable_default_repr(obj):
    try:
        return str(obj)
    except Exception:
        log.warning("I/O object is neither JSON serializable nor string-able. Defaulting to placeholder value instead.")
        return "[Unserializable object: {}]".format(repr(obj))


def safe_json(obj, ensure_ascii=True):
    if isinstance(obj, str):
        return obj
    try:
        # If object is a Pydantic model, convert to JSON serializable dict first using model_dump()
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            obj = obj.model_dump()
        return json.dumps(obj, ensure_ascii=ensure_ascii, skipkeys=True, default=_unserializable_default_repr)
    except Exception:
        log.error("Failed to serialize object to JSON.", exc_info=True)


def load_data_value(value):
    if isinstance(value, (list, tuple, set)):
        return [load_data_value(item) for item in value]
    elif isinstance(value, dict):
        return {str(k): load_data_value(v) for k, v in value.items()}
    elif hasattr(value, "model_dump"):
        return value.model_dump()
    elif is_dataclass(value):
        return asdict(value)
    elif isinstance(value, (int, float, str, bool)) or value is None:
        return value
    else:
        value_str = safe_json(value)
        try:
            return json.loads(value_str)
        except json.JSONDecodeError:
            return value_str


def add_span_link(span: Span, span_id: str, trace_id: str, from_io: str, to_io: str) -> None:
    current_span_links = span._get_ctx_item(SPAN_LINKS) or []
    current_span_links.append(
        {
            "span_id": span_id,
            "trace_id": trace_id,
            "attributes": {"from": from_io, "to": to_io},
        }
    )
    span._set_ctx_item(SPAN_LINKS, current_span_links)


def enforce_message_role(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Enforce that each message includes a role (empty "" by default) field."""
    for message in messages:
        message.setdefault("role", "")
    return messages


def convert_tags_dict_to_list(tags: Dict[str, str]) -> List[str]:
    if not tags:
        return []
    return [f"{key}:{value}" for key, value in tags.items()]


@dataclass
class TrackedToolCall:
    """
    Holds information about a tool call and it's associated LLM/Tool spans
    for span linking purposes.
    """

    tool_id: str
    tool_name: str
    arguments: str
    llm_span_context: Dict[str, str]  # span/trace id of the LLM span that initiated this tool call
    tool_span_context: Optional[Dict[str, str]] = None  # span/trace id of the tool span that executed this call
    is_handoff_completed: bool = False  # Track if handoff is completed to noisy links
    tool_kind: str = "function"  # one of "function", "handoff"


class ToolCallTracker:
    """Used to track tool data and their associated llm/tool spans for span linking."""

    def __init__(self):
        self._tool_calls: Dict[str, TrackedToolCall] = {}  # maps tool id's to tool call data
        self._lookup_tool_id: Dict[Tuple[str, str], str] = {}  # maps (tool_name, arguments) to tool id's

    def on_llm_tool_choice(
        self, tool_id: str, tool_name: str, arguments: str, llm_span_context: Dict[str, str]
    ) -> None:
        """
        Called when an llm span finishes. This is used to save the tool choice information generated by an LLM
        for
        """
        tool_call = TrackedToolCall(
            tool_id=tool_id,
            tool_name=tool_name,
            arguments=arguments,
            llm_span_context=llm_span_context,
        )
        self._tool_calls[tool_id] = tool_call
        self._lookup_tool_id[(tool_name, arguments)] = tool_id

    def on_tool_call(self, tool_name: str, tool_arg: str, tool_kind: str, tool_span: Span) -> None:
        """
        Called when a tool span finishes. This is used to link the input of the tool span to the output
        of the LLM span responsible for generating it's input. We also save the span/trace id of the tool call
        so that we can link to the input of an LLM span if it's output is used in an LLM call.
        """
        tool_id = self._lookup_tool_id.get((tool_name, tool_arg))
        if not tool_id:
            return
        tool_call = self._tool_calls.get(tool_id)
        if not tool_call:
            return
        add_span_link(
            tool_span,
            tool_call.llm_span_context["span_id"],
            tool_call.llm_span_context["trace_id"],
            "output",
            "input",
        )
        self._tool_calls[tool_id].tool_span_context = {
            "span_id": str(tool_span.span_id),
            "trace_id": format_trace_id(tool_span.trace_id),
        }
        self._tool_calls[tool_id].tool_kind = tool_kind
        self._lookup_tool_id.pop((tool_name, tool_arg))

    def on_tool_call_output_used(self, tool_id: str, llm_span: Span) -> None:
        """
        Called when an LLM span finishes. This is used to link the output of a tool call to the input of an
        LLM span.

        For handoff tool calls, we need to mark the handoff as completed since we only want to link
        the output of a handoff tool span to the FIRST LLM call that has that handoff as input.

        For function tool calls, the tool output is used as context for every LLM call that has that tool call
        as an input.
        """
        tool_call = self._tool_calls.get(tool_id)
        if not tool_call or not tool_call.tool_span_context or tool_call.is_handoff_completed:
            return

        if tool_call.tool_kind == "handoff":
            self._tool_calls[tool_id].is_handoff_completed = True

        add_span_link(
            llm_span,
            tool_call.tool_span_context["span_id"],
            tool_call.tool_span_context["trace_id"],
            "output",
            "input",
        )
