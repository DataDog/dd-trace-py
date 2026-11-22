from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.internal.utils.streaming import AsyncStreamHandler
from ddtrace.internal.utils.streaming import StreamHandler


log = get_logger(__name__)

UNKNOWN_MODEL_PROVIDER = "unknown"
UNKNOWN_MODEL_NAME = "unknown"

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
    kwargs: Optional[dict[str, Any]] = None, instance: Any = None, model_name_attr: Optional[str] = None
) -> tuple[str, str]:
    """
    Function to extract provider and model name from either kwargs or instance attributes.
    Args:
        kwargs: Dictionary containing model information (used for google_genai)
        instance: Model instance with attributes (used for vertexai)
        model_name_attr: Attribute name to extract from instance (e.g., "_model_name", "model_name", used for vertexai)

    Returns:
        tuple of (provider_name, model_name)
    """
    model_path = ""
    if kwargs is not None:
        model_path = kwargs.get("model", "")
    elif instance is not None and model_name_attr is not None:
        model_path = _get_attr(instance, model_name_attr, "")

    if not model_path or not isinstance(model_path, str):
        return UNKNOWN_MODEL_PROVIDER, UNKNOWN_MODEL_NAME

    model_name = model_path.split("/")[-1] if "/" in model_path else model_path

    for prefix in KNOWN_MODEL_PREFIX_TO_PROVIDER.keys():
        if model_name.lower().startswith(prefix):
            provider_name = KNOWN_MODEL_PREFIX_TO_PROVIDER[prefix]
            return provider_name, model_name
    return UNKNOWN_MODEL_PROVIDER, model_name if model_name else UNKNOWN_MODEL_NAME


def _join_chunks(chunks: list[Any]) -> Optional[dict[str, Any]]:
    """
    Consolidates streamed response GenerateContentResponse chunks into a single dictionary representing the response.
    All chunks should have the same role since one generation call produces consistent content type.
    """
    if not chunks:
        return None

    thought_chunks = []
    text_chunks = []
    non_text_parts = []
    role = None

    try:
        for chunk in chunks:
            candidates = _get_attr(chunk, "candidates", [])
            for candidate in candidates:
                content = _get_attr(candidate, "content", None)
                if not content:
                    continue

                if role is None:
                    role = _get_attr(content, "role", GOOGLE_GENAI_DEFAULT_MODEL_ROLE)

                parts = _get_attr(content, "parts", [])
                for part in parts:
                    text = _get_attr(part, "text", None)
                    if not text:
                        non_text_parts.append(part)
                    elif _get_attr(part, "thought", False):
                        thought_chunks.append(text)
                    else:
                        text_chunks.append(text)

        parts = []
        if thought_chunks:
            parts.append({"text": "".join(thought_chunks), "thought": True})
        if text_chunks:
            parts.append({"text": "".join(text_chunks)})
        parts.extend(non_text_parts)

        merged_response = {"candidates": [{"content": {"role": role, "parts": parts}}] if parts else []}

        last_chunk = chunks[-1]
        merged_response["usage_metadata"] = _get_attr(last_chunk, "usage_metadata", {})

        return merged_response
    except Exception:
        # if error processing chunks, return None to avoid crashing the user app
        return None


class BaseGoogleGenAIStreamHandler:
    """Mixed into StreamHandler/AsyncStreamHandler, which provide chunks and options."""

    chunks: list[Any]
    options: dict[str, Any]

    def finalize_stream(self, exception: Optional[BaseException] = None) -> None:
        """Build the response from chunks, then dispatch the deferred ended event, which finishes the span."""
        ctx = self.options["ctx"]
        try:
            ctx.event.response = _join_chunks(self.chunks)
        except Exception:
            log.warning("Error processing streamed Google GenAI response.", exc_info=True)
        if exception:
            ctx.dispatch_ended_event(type(exception), exception, exception.__traceback__)
        else:
            ctx.dispatch_ended_event()


class GoogleGenAIStreamHandler(BaseGoogleGenAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)


class GoogleGenAIAsyncStreamHandler(BaseGoogleGenAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)
