from hashlib import sha1
import json
from re import match
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import GEMINI_APM_SPAN_NAME
from ddtrace.llmobs._constants import DEFAULT_PROMPT_NAME
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS
from ddtrace.llmobs._constants import IS_EVALUATION_SPAN
from ddtrace.llmobs._constants import LANGCHAIN_APM_SPAN_NAME
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OPENAI_APM_SPAN_NAME
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import VERTEXAI_APM_SPAN_NAME
from ddtrace.trace import Span


log = get_logger(__name__)


def validate_prompt(prompt: dict, ml_app:str="") -> Dict[str, Union[str, Dict[str, Any], List[str], List[Tuple[str, str]]]]:
    validated_prompt = {}  # type: Dict[str, Union[str, Dict[str,Any], List[str], List[Tuple[str, str]]]]

    if not isinstance(prompt, dict):
        raise TypeError("Prompt must be a dictionary")

    name = prompt.get("name")
    variables = prompt.get("variables")
    template = prompt.get("template")
    version = prompt.get("version")
    prompt_id = prompt.get("id")
    example_variable_keys = prompt.get("example_variables")
    constraint_variable_keys = prompt.get("constraint_variables")
    ctx_variable_keys = prompt.get("rag_context_variables")
    rag_query_variable_keys = prompt.get("rag_query_variables")

    if name is not None:
        if not isinstance(name, str):
            raise TypeError("Prompt name must be a string.")
        validated_prompt["name"] = name
    elif prompt_id is not None:
        validated_prompt["name"] = prompt_id
    else:
        validated_prompt["name"] = DEFAULT_PROMPT_NAME

    if version is not None:
        semver_regex = (
            r"^(?P<major>0|[1-9]\d*)\."
            r"(?P<minor>0|[1-9]\d*)\."
            r"(?P<patch>0|[1-9]\d*)"
            r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-]"
            r"[0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-]"
            r"[0-9a-zA-Z-]*))*))?"
            r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+"
            r"(?:\.[0-9a-zA-Z-]+)*))?$"
        )
        if not isinstance(version, str):
            raise TypeError("Prompt version must be a string.")
        if not bool(match(semver_regex, version)):
            log.warning(
                "Prompt version must be semver compatible. Please check https://semver.org/ for more information."
            )
        # Add minor and patch version if not present
        version_parts = (version.split(".") + ["0", "0"])[:3]
        version = ".".join(version_parts)
        validated_prompt["version"] = version
    else:
        validated_prompt["version"] = "1.0.0"

    if variables is not None:
        if not isinstance(variables, dict):
            raise TypeError("Prompt variables must be a dictionary.")
        if not any(isinstance(k, str) for k in variables):
            raise TypeError("Prompt variable keys must be strings.")
        # check if values are json serializable
        if not all(isinstance(v, (str, int, float, bool, list, dict)) for v in variables.values()):
            raise TypeError("Prompt variable values must be JSON serializable.")
        validated_prompt["variables"] = variables

    if template is not None:
        if isinstance(template, str):
            template = [("user", template)]
        # check if template is a list of tuples of 2 strings
        if (
            not isinstance(template, list)
            or not all(isinstance(t, tuple) for t in template)
            or not all(len(t) == 2 for t in template)
            or not all(isinstance(t[0], str) and isinstance(t[1], str) for t in template)
        ):
            raise TypeError("Prompt template must be a list of 2-tuples (role,content).")
        validated_prompt["template"] = template

    if example_variable_keys is not None:
        if not isinstance(example_variable_keys, list) or not all(isinstance(k, str) for k in example_variable_keys):
            raise TypeError("Prompt field `example_variables` must be a list of strings.")
        validated_prompt["example_variable_keys"] = example_variable_keys
    elif "examples" in variables:
        validated_prompt["example_variable_keys"] = ["examples"]

    if constraint_variable_keys is not None:
        if not isinstance(constraint_variable_keys, list):
            raise TypeError("Prompt field `constraint_variables` must be a list of strings.")
        if not all(isinstance(k, str) for k in constraint_variable_keys):
            raise TypeError("Prompt field `constraint_variables` must be a list of strings.")
        validated_prompt["constraint_variable_keys"] = constraint_variable_keys
    elif "constraints" in variables:
        validated_prompt["constraint_variable_keys"] = ["constraints"]

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

    if prompt_id is not None:
        if not isinstance(prompt_id, str):
            raise TypeError("Prompt ID must be a string.")
        validated_prompt["id"] = prompt_id
    else:
        log.warning("Prompt ID is not provided. The prompt ID will be generated based on the prompt name.")
        if name is not None:
            validated_prompt["id"] = name or DEFAULT_PROMPT_NAME

    # Compute prompt instance id
    validated_prompt["prompt_instance_id"] = _get_prompt_instance_id(validated_prompt, ml_app)

    return validated_prompt


def _get_prompt_instance_id(validated_prompt: dict, ml_app: str) -> str:
    name = validated_prompt.get("name")
    variables = validated_prompt.get("variables")
    template = validated_prompt.get("template")
    version = validated_prompt.get("version")
    prompt_id = validated_prompt.get("id")
    example_variable_keys = validated_prompt.get("example_variables")
    constraint_variable_keys = validated_prompt.get("constraint_variables")
    ctx_variable_keys = validated_prompt.get("rag_context_variables")
    rag_query_variable_keys = validated_prompt.get("rag_query_variables")

    instance_id_str = (f"[{ml_app}]{prompt_id}"
                       f"{name}{prompt_id}{version}{template}{variables}"
                       f"{example_variable_keys}{constraint_variable_keys}"
                       f"{ctx_variable_keys}{rag_query_variable_keys}")

    prompt_instance_id = sha1(instance_id_str.encode()).hexdigest()

    return prompt_instance_id


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
    if span.name in (LANGCHAIN_APM_SPAN_NAME, GEMINI_APM_SPAN_NAME, VERTEXAI_APM_SPAN_NAME) and span.resource != "":
        return span.resource
    elif span.name == OPENAI_APM_SPAN_NAME and span.resource != "":
        client_name = span.get_tag("openai.request.client") or "OpenAI"
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


def _get_ml_app(span: Span) -> str:
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
    return ml_app or config._llmobs_ml_app or "unknown-ml-app"


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
    default_repr = "[Unserializable object: {}]".format(repr(obj))
    log.warning("I/O object is not JSON serializable. Defaulting to placeholder value instead.")
    return default_repr


def safe_json(obj, ensure_ascii=True):
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=ensure_ascii, skipkeys=True, default=_unserializable_default_repr)
    except Exception:
        log.error("Failed to serialize object to JSON.", exc_info=True)
