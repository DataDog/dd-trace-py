from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.llmobs._constants import BILLABLE_CHARACTER_COUNT_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json


# Google GenAI has roles "model" and "user", but in order to stay consistent with other integrations,
# we use "assistant" as the default role for model messages
GOOGLE_GENAI_DEFAULT_MODEL_ROLE = "assistant"

# https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-partner-models
# GeminiAPI: only exports google provided models
# VertexAI: can map provided models to provider based on prefix, a best effort mapping
# as huggingface exports hundreds of custom provided models
KNOWN_MODEL_PREFIX_TO_PROVIDER = {
    "gemini": "google",
    "imagen": "google",
    "veo": "google",
    "text-embedding": "google",
    "jamba": "ai21",
    "claude": "anthropic",
    "llama": "meta",
    "mistral": "mistral",
    "codestral": "mistral",
    "deepseek": "deepseek",
    "olmo": "ai2",
    "tulu": "ai2",
    "molmo": "ai2",
    "specter": "ai2",
    "cosmoo": "ai2",
    "qodo": "qodo",
    "mars": "camb.ai",
}


def extract_provider_and_model_name(
    kwargs: Optional[Dict[str, Any]] = None, instance: Any = None, model_name_attr: Optional[str] = None
) -> Tuple[str, str]:
    """
    Function to extract provider and model name from either kwargs or instance attributes.
    Args:
        kwargs: Dictionary containing model information (used for google_genai)
        instance: Model instance with attributes (used for vertexai and google_generativeai)
        model_name_attr: Attribute name to extract from instance (e.g., "_model_name", "model_name", used for vertexai
                         and google_generativeai)

    Returns:
        Tuple of (provider_name, model_name)
    """
    model_path = ""
    if kwargs is not None:
        model_path = kwargs.get("model", "")
    elif instance is not None and model_name_attr is not None:
        model_path = _get_attr(instance, model_name_attr, "")

    if not model_path or not isinstance(model_path, str):
        return "custom", "custom"

    model_name = model_path.split("/")[-1] if "/" in model_path else model_path

    for prefix in KNOWN_MODEL_PREFIX_TO_PROVIDER.keys():
        if model_name.lower().startswith(prefix):
            provider_name = KNOWN_MODEL_PREFIX_TO_PROVIDER[prefix]
            return provider_name, model_name
    return "custom", model_name if model_name else "custom"


def normalize_contents_google_genai(contents) -> List[Dict[str, Any]]:
    """
    contents has a complex union type structure:
    - contents: Union[ContentListUnion, ContentListUnionDict]
    - ContentListUnion = Union[list[ContentUnion], ContentUnion]
    - ContentListUnionDict = Union[list[ContentUnionDict], ContentUnionDict]
    - ContentUnion = Union[Content, list[PartUnion], PartUnion]
    - PartUnion = Union[File, Part, str]

    Can also be used for system_instruction which has type ContentUnion

    This function normalizes all these variants into a list of dicts with format{"role": role, "parts": parts}
    """

    def extract_content(content):
        role = _get_attr(content, "role", None)
        parts = _get_attr(content, "parts", None)

        # if parts is missing and content itself is a PartUnion or list[PartUnion]
        if parts is None:
            if isinstance(content, list):
                parts = content
            else:
                parts = [content]

        elif not isinstance(parts, list):
            parts = [parts]

        return {"role": role, "parts": parts}

    if isinstance(contents, list):
        return [extract_content(c) for c in contents]
    return [extract_content(contents)]


def extract_generation_metrics_google_genai(response) -> Dict[str, Any]:
    if not response:
        return {}

    usage_metadata = _get_attr(response, "usage_metadata", {})

    usage = {}
    input_tokens = _get_attr(usage_metadata, "prompt_token_count", None)

    candidates_tokens = _get_attr(usage_metadata, "candidates_token_count", None)
    thought_tokens = _get_attr(usage_metadata, "thoughts_token_count", None)
    if candidates_tokens is not None or thought_tokens is not None:
        output_tokens = (candidates_tokens or 0) + (thought_tokens or 0)
    else:
        output_tokens = None

    cached_tokens = _get_attr(usage_metadata, "cached_content_token_count", None)
    total_tokens = _get_attr(usage_metadata, "total_token_count", None) or input_tokens + output_tokens

    if input_tokens is not None:
        usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if cached_tokens is not None:
        usage[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cached_tokens
    if total_tokens is not None:
        usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens

    return usage


def extract_embedding_metrics_google_genai(response) -> Dict[str, Any]:
    if not response:
        return {}
    usage = {}
    metadata = _get_attr(response, "metadata", {})
    billable_character_count = _get_attr(metadata, "billable_character_count", None)
    input_tokens = 0
    embeddings = _get_attr(response, "embeddings", [])
    for embedding in embeddings if embeddings is not None else []:
        statistics = _get_attr(embedding, "statistics", {})
        input_tokens += _get_attr(statistics, "token_count", 0)

    if billable_character_count is not None:
        usage[BILLABLE_CHARACTER_COUNT_METRIC_KEY] = billable_character_count
    if input_tokens:  # falsy value of 0 should not be set
        usage[INPUT_TOKENS_METRIC_KEY] = int(input_tokens)

    return usage


def extract_message_from_part_google_genai(part, role: str) -> Dict[str, Any]:
    """part is a PartUnion = Union[File, Part, PIL_Image, str]

    returns a dict representing a message with format {"role": role, "content": content}
    """
    if role == "model":
        role = GOOGLE_GENAI_DEFAULT_MODEL_ROLE

    message: Dict[str, Any] = {"role": role}
    if isinstance(part, str):
        message["content"] = part
        return message

    thought = _get_attr(part, "thought", False)
    if thought:
        message["role"] = "reasoning"

    text = _get_attr(part, "text", None)
    if text:
        message["content"] = str(text)
        return message

    function_call = _get_attr(part, "function_call", None)
    if function_call:
        function_name = _get_attr(function_call, "name", "")
        function_args = _get_attr(function_call, "args", {})
        message["tool_calls"] = [{"name": str(function_name), "arguments": function_args}]
        return message

    function_response = _get_attr(part, "function_response", None)
    if function_response:
        message["role"] = "tool"
        message["content"] = str(_get_attr(function_response, "response", ""))
        message["tool_id"] = _get_attr(function_response, "id", "")
        return message

    executable_code = _get_attr(part, "executable_code", None)
    if executable_code:
        language = _get_attr(executable_code, "language", "UNKNOWN")
        code = _get_attr(executable_code, "code", "")
        message["content"] = safe_json({"language": str(language), "code": str(code)})
        return message

    code_execution_result = _get_attr(part, "code_execution_result", None)
    if code_execution_result:
        outcome = _get_attr(code_execution_result, "outcome", "OUTCOME_UNSPECIFIED")
        output = _get_attr(code_execution_result, "output", "")
        message["content"] = safe_json({"outcome": str(outcome), "output": str(output)})
        return message

    return {"content": "Unsupported file type: {}".format(type(part)), "role": role}


def llmobs_get_metadata_gemini_vertexai(kwargs, instance):
    metadata = {}
    model_config = getattr(instance, "_generation_config", {}) or {}
    model_config = model_config.to_dict() if hasattr(model_config, "to_dict") else model_config
    request_config = kwargs.get("generation_config", {}) or {}
    request_config = request_config.to_dict() if hasattr(request_config, "to_dict") else request_config

    parameters = ("temperature", "max_output_tokens", "candidate_count", "top_p", "top_k")
    for param in parameters:
        model_config_value = _get_attr(model_config, param, None)
        request_config_value = _get_attr(request_config, param, None)
        if model_config_value or request_config_value:
            metadata[param] = request_config_value or model_config_value
    return metadata


def extract_message_from_part_gemini_vertexai(part, role=None):
    text = _get_attr(part, "text", "")
    function_call = _get_attr(part, "function_call", None)
    function_response = _get_attr(part, "function_response", None)
    message = {"content": text}
    if role:
        message["role"] = role
    if function_call:
        function_call_dict = function_call
        if not isinstance(function_call, dict):
            function_call_dict = type(function_call).to_dict(function_call)
        message["tool_calls"] = [
            {"name": function_call_dict.get("name", ""), "arguments": function_call_dict.get("args", {})}
        ]
    if function_response:
        function_response_dict = function_response
        if not isinstance(function_response, dict):
            function_response_dict = type(function_response).to_dict(function_response)
        message["content"] = "[tool result: {}]".format(function_response_dict.get("response", ""))
    return message


def get_system_instructions_gemini_vertexai(model_instance):
    """
    Extract system instructions from model and convert to []str for tagging.
    """
    try:
        from google.ai.generativelanguage_v1beta.types.content import Content
    except ImportError:
        Content = None
    try:
        from vertexai.generative_models._generative_models import Part
    except ImportError:
        Part = None

    raw_system_instructions = getattr(model_instance, "_system_instruction", [])
    if Content is not None and isinstance(raw_system_instructions, Content):
        system_instructions = []
        for part in raw_system_instructions.parts:
            system_instructions.append(_get_attr(part, "text", ""))
        return system_instructions
    elif isinstance(raw_system_instructions, str):
        return [raw_system_instructions]
    elif Part is not None and isinstance(raw_system_instructions, Part):
        return [_get_attr(raw_system_instructions, "text", "")]
    elif not isinstance(raw_system_instructions, list):
        return []

    system_instructions = []
    for elem in raw_system_instructions:
        if isinstance(elem, str):
            system_instructions.append(elem)
        elif Part is not None and isinstance(elem, Part):
            system_instructions.append(_get_attr(elem, "text", ""))
    return system_instructions
