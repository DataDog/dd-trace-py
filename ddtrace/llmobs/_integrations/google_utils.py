from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from ddtrace.llmobs._constants import BILLABLE_CHARACTER_COUNT_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._utils import _get_attr


# google genai has roles "model" and "user", but in order to stay consistent with other integrations,
# we use "assistant" as the default role for model messages
DEFAULT_MODEL_ROLE = "assistant"

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


def extract_provider_and_model_name(kwargs: Dict[str, Any]) -> Tuple[str, str]:
    model_path = kwargs.get("model", "")
    model_name = model_path.split("/")[-1]
    for prefix in KNOWN_MODEL_PREFIX_TO_PROVIDER.keys():
        if model_name.lower().startswith(prefix):
            provider_name = KNOWN_MODEL_PREFIX_TO_PROVIDER[prefix]
            return provider_name, model_name
    return "custom", model_name if model_name else "custom"


def normalize_contents(contents) -> List[Dict[str, Any]]:
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
        role = DEFAULT_MODEL_ROLE

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
        message["content"] = {"language": str(language), "code": str(code)}
        return message

    code_execution_result = _get_attr(part, "code_execution_result", None)
    if code_execution_result:
        outcome = _get_attr(code_execution_result, "outcome", "OUTCOME_UNSPECIFIED")
        output = _get_attr(code_execution_result, "output", "")
        message["content"] = {"outcome": str(outcome), "output": str(output)}
        return message

    return {"content": "Unsupported file type: {}".format(type(part)), "role": role}
