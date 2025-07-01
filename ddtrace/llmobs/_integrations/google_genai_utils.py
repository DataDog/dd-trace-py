from ddtrace.llmobs._utils import _get_attr

from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY

# https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-partner-models
# GeminiAPI: only exports google provided models
# VertexAI: can map provided models to provider based on prefix, a best effort mapping
# as huggingface exports hundreds of custom provided models
KNOWN_MODEL_PREFIX_TO_PROVIDER = {
    "gemini": "google",
    "imagen": "google",
    "veo": "google",
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


def extract_provider_and_model_name(kwargs):
    model_path = kwargs.get("model", "")
    model_name = model_path.split("/")[-1]
    for prefix in KNOWN_MODEL_PREFIX_TO_PROVIDER.keys():
        if model_name.lower().startswith(prefix):
            provider_name = KNOWN_MODEL_PREFIX_TO_PROVIDER[prefix]
            return provider_name, model_name
    return "custom", model_name if model_name else "custom"

def normalize_contents(contents):
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


def extract_metrics_google_genai(response):
    if not response:
        return {}

    usage_metadata = _get_attr(response, "usage_metadata", {})

    usage = {}
    input_tokens = _get_attr(usage_metadata, "prompt_token_count", None)
    output_tokens = _get_attr(usage_metadata, "candidates_token_count", None)
    cached_tokens = _get_attr(usage_metadata, "cached_content_token_count", None)
    total_tokens = _get_attr(usage_metadata, "total_token_count", None) or input_tokens + output_tokens

    if input_tokens is not None:
        usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if cached_tokens is not None:
        usage["cached_tokens"] = cached_tokens  # TODO(max): add constant for cached tokens when it is introduced
    if total_tokens is not None:
        usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens

    return usage


# TODO(max): check for cases where more than one field is set per part
def extract_message_from_part_google_genai(part, role):
    """part is a PartUnion = Union[File, Part, PIL_Image, str]

    returns a dict representing a message with format {"role": role, "content": content}
    """
    message = {"role": role}
    if isinstance(part, str):
        message["content"] = part
        return message

    thought = _get_attr(part, "thought", False)
    if thought:
        message["role"] = "reasoning"

    text = _get_attr(part, "text", None)
    if text:
        message["content"] = text
        return message

    function_call = _get_attr(part, "function_call", None)
    if function_call and callable(getattr(function_call, "model_dump", None)):
        function_call_dict = function_call.model_dump()
        message["tool_calls"] = [
            {"name": function_call_dict.get("name", ""), "arguments": function_call_dict.get("args", {})}
        ]
        return message

    function_response = _get_attr(part, "function_response", None)
    if function_response and callable(getattr(function_response, "model_dump", None)):
        function_response_dict = function_response.model_dump()
        message["content"] = "[tool result: {}]".format(function_response_dict.get("response", ""))
        return message

    executable_code = _get_attr(part, "executable_code", None)
    if executable_code:
        language = _get_attr(executable_code, "language", "UNKNOWN")
        code = _get_attr(executable_code, "code", "")
        message["content"] = {"language": language, "code": code}
        return message

    code_execution_result = _get_attr(part, "code_execution_result", None)
    if code_execution_result:
        outcome = _get_attr(code_execution_result, "outcome", "OUTCOME_UNSPECIFIED")
        output = _get_attr(code_execution_result, "output", "")
        message["content"] = {"outcome": outcome, "output": output}
        return message

    return {"content": "Unsupported file type: {}".format(type(part)), "role": role}


def process_response(response):
    messages = []
    candidates = _get_attr(response, "candidates", [])
    for candidate in candidates:
        content = _get_attr(candidate, "content", None)
        if not content:
            continue
        parts = _get_attr(content, "parts", [])
        role = _get_attr(content, "role", None) or "model"
        for part in parts:
            message = extract_message_from_part_google_genai(part, role)
            messages.append(message)
    return messages