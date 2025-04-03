from hashlib import sha256
import json
from re import compile
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import DEFAULT_PROMPT_NAME
from ddtrace.llmobs._constants import GEMINI_APM_SPAN_NAME
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS
from ddtrace.llmobs._constants import IS_EVALUATION_SPAN
from ddtrace.llmobs._constants import LANGCHAIN_APM_SPAN_NAME
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OPENAI_APM_SPAN_NAME
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import VERTEXAI_APM_SPAN_NAME
from ddtrace.llmobs.utils import Message
from ddtrace.llmobs.utils import Prompt
from ddtrace.trace import Span


log = get_logger(__name__)

# SemVer regex from https://semver.org/
SEMVER_PATTERN_COMPILED = compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-]"
    r"[0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-]"
    r"[0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+"
    r"(?:\.[0-9a-zA-Z-]+)*))?$"
)

PromptDict = Dict[str, Union[str, Dict[str, Any], List[str], List[Dict[str, str]], List[Message]]]


def validate_prompt(
    prompt: Union[Dict[str, Any], Prompt], ml_app: str = "", strict_validation: bool = True
) -> Dict[str, Any]:
    # Stage 0: Check if dict
    if not isinstance(prompt, dict):
        raise TypeError(f"Prompt must be a dictionary, got {type(prompt).__name__}.")

    # Stage 1: Extract values
    name = prompt.get("name")
    prompt_id = prompt.get("id")
    version = prompt.get("version")
    variables = prompt.get("variables")
    template = prompt.get("template")
    chat_template = prompt.get("chat_template")
    ctx_variable_keys = prompt.get("rag_context_variables")
    query_variable_keys = prompt.get("rag_query_variables")

    # Stage 2: Strict validations
    if strict_validation:
        if prompt_id is None:
            raise ValueError("'id' is mandatory under strict validation.")

        if version is not None:
            # Normalize version to full semver (fill minor/patch if omitted)
            version_parts = (version.split(".") + ["0", "0"])[:3]
            version = ".".join(version_parts)
            if not SEMVER_PATTERN_COMPILED.match(version):
                raise ValueError(f"'version' must be semver compatible, but got '{version}'.")

        if template is None and chat_template is None:
            raise ValueError("Either 'template' or 'chat_template' must be provided.")

    # Stage 3: Set defaults
    final_prompt_id = prompt_id or name or DEFAULT_PROMPT_NAME
    final_name = name or prompt_id or DEFAULT_PROMPT_NAME
    final_version = version or "1.0.0"
    final_ctx_variable_keys = ctx_variable_keys or ["context"]
    final_query_variable_keys = query_variable_keys or ["question"]

    # Stage 4: Type checks
    if not isinstance(final_prompt_id, str):
        raise TypeError(f"'id' must be str, got {type(final_prompt_id).__name__}.")

    if not isinstance(final_name, str):
        raise TypeError(f"'name' must be str, got {type(final_name).__name__}.")

    if not isinstance(final_version, str):
        try:
            final_version = str(final_version)
        except Exception:
            raise TypeError(f"'version' must be str, got {type(final_version).__name__}.")

    if not (isinstance(final_ctx_variable_keys, list) and all(isinstance(i, str) for i in final_ctx_variable_keys)):
        raise TypeError("'rag_context_variables' must be a List[str].")

    if not (isinstance(final_query_variable_keys, list) and all(isinstance(i, str) for i in final_query_variable_keys)):
        raise TypeError("'rag_query_variables' must be a List[str].")

    if template is not None and not isinstance(template, str):
        raise TypeError(f"'template' must be str, got {type(template).__name__}.")

    if chat_template is not None:
        if not isinstance(chat_template, list):
            raise TypeError("'chat_template' must be a list.")
        for ct in chat_template:
            if not (
                (isinstance(ct, tuple) and len(ct) == 2 and all(isinstance(e, str) for e in ct))
                or (isinstance(ct, dict) and all(key in ["role", "content"] for key in ct))
            ):
                raise TypeError("Each 'chat_template' entry should be Message, tuple[str,str], or dict[str,str].")

    if variables is not None:
        if not isinstance(variables, dict):
            raise TypeError(f"'variables' must be Dict[str,Any], got {type(variables).__name__}")
        if not all(isinstance(k, str) for k in variables):
            raise TypeError("Keys of 'variables' must all be strings.")
        if not all(isinstance(v, (str, int, float, bool, list, dict)) for v in variables.values()):
            raise TypeError("Values of 'variables' must be JSON serializable.")

    # Stage 5: Transformations
    # Normalize version to full semver (fill minor/patch if omitted)
    version_parts = (final_version.split(".") + ["0", "0"])[:3]
    final_version = ".".join(version_parts)
    if not SEMVER_PATTERN_COMPILED.match(final_version):
        log.warning("'version' not semver compatible", final_version)

    # Ensure chat_template is standardized List[dict[role:str, content:str]]
    final_chat_template = None
    if chat_template:
        final_chat_template = []
        for msg in chat_template:
            if isinstance(msg, tuple):
                role, content = msg
                final_chat_template.append(Message(role=role, content=content))
            elif isinstance(msg, dict):
                final_chat_template.append(Message(role=msg["role"], content=msg["content"]))

    # Stage 6: Hash Generation
    instance_id = _get_prompt_instance_id(
        {
            "id": final_prompt_id,
            "name": final_name,
            "version": final_version,
            "template": template,
            "chat_template": final_chat_template,
            "variables": variables,
            "rag_context_variables": final_ctx_variable_keys,
            "rag_query_variables": final_query_variable_keys,
        },
        ml_app,
    )

    # Stage 7: Produce output
    validated_prompt: PromptDict = {}
    if final_prompt_id:
        validated_prompt["id"] = final_prompt_id
    if final_name:
        validated_prompt["name"] = final_name
    if final_version:
        validated_prompt["version"] = final_version
    if variables:
        validated_prompt["variables"] = variables
    if template:
        validated_prompt["template"] = template
    if final_chat_template:
        validated_prompt["chat_template"] = final_chat_template
    if final_ctx_variable_keys:
        validated_prompt[INTERNAL_CONTEXT_VARIABLE_KEYS] = final_ctx_variable_keys
    if final_query_variable_keys:
        validated_prompt[INTERNAL_QUERY_VARIABLE_KEYS] = final_query_variable_keys
    if instance_id:
        validated_prompt["instance_id"] = instance_id

    return validated_prompt


def _get_prompt_instance_id(validated_prompt: dict, ml_app: str = "") -> str:
    name = validated_prompt.get("name")
    variables = validated_prompt.get("variables")
    template = validated_prompt.get("template")
    chat_template = validated_prompt.get("chat_template")
    version = validated_prompt.get("version")
    prompt_id = validated_prompt.get("id")
    ctx_variable_keys = validated_prompt.get("rag_context_variables")
    rag_query_variable_keys = validated_prompt.get("rag_query_variables")

    instance_id_str = (
        f"[{ml_app}]{prompt_id}"
        f"{name}{prompt_id}{version}{template}{chat_template}{variables}"
        f"{ctx_variable_keys}{rag_query_variable_keys}"
    )

    hasher = sha256()
    hasher.update(instance_id_str.encode("utf-8"))
    instance_id = hasher.hexdigest()

    return instance_id


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
