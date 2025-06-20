import sys
import wrapt
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY


def normalize_contents(contents):
    """
    Contents has a complex union type structure:
    - contents: Union[ContentListUnion, ContentListUnionDict]
    - ContentListUnion = Union[list[ContentUnion], ContentUnion]  
    - ContentListUnionDict = Union[list[ContentUnionDict], ContentUnionDict]
    
    This function normalizes all these variants into a list of dicts
    """
    def extract_content(content):
        role = _get_attr(content, "role", None)
        parts = _get_attr(content, "parts", None)

        #if parts is missing and content itself is a PartUnion or list[PartUnion]
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

# since we are not setting metrics in APM span, can't call get_llmobs_metrics_tags
def extract_metrics_google_genai(response):
    usage = {}

    if not response:
        return usage
    
    usage_metadata = _get_attr(response, "usage_metadata", {})

    input_tokens = _get_attr(usage_metadata, "prompt_token_count", None)
    output_tokens = _get_attr(usage_metadata, "candidates_token_count", None)
    total_tokens = _get_attr(usage_metadata, "total_token_count", None) or input_tokens + output_tokens

    if input_tokens is not None:
        usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if total_tokens is not None:
        usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
    return usage

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


class BaseTracedGoogleGenAIStreamResponse(wrapt.ObjectProxy):
    def __init__(self, wrapped, integration, span, args, kwargs):
        super().__init__(wrapped)
        self._self_dd_span = span
        self._self_chunks = []
        self._self_args = args
        self._self_kwargs = kwargs
        self._self_dd_integration = integration


class TracedGoogleGenAIStreamResponse(BaseTracedGoogleGenAIStreamResponse):
    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            self._self_chunks.append(chunk)
            return chunk
        except StopIteration:
            if self._self_dd_integration.is_pc_sampled_llmobs(self._self_dd_span):
                self._self_dd_integration.llmobs_set_tags(
                    self._self_dd_span, args=self._self_args, kwargs=self._self_kwargs, response=self.__wrapped__
                )
            self._self_dd_span.finish()
            raise
        except Exception:
            self._self_dd_span.set_exc_info(*sys.exc_info())
            self._self_dd_span.finish()
            raise


class TracedAsyncGoogleGenAIStreamResponse(BaseTracedGoogleGenAIStreamResponse):
    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            self._self_chunks.append(chunk)
            return chunk
        except StopAsyncIteration:
            if self._self_dd_integration.is_pc_sampled_llmobs(self._self_dd_span):
                self._self_dd_integration.llmobs_set_tags(
                    self._self_dd_span, args=self._self_args, kwargs=self._self_kwargs, response=self.__wrapped__
                )
            self._self_dd_span.finish()
            raise
        except Exception:
            self._self_dd_span.set_exc_info(*sys.exc_info())
            self._self_dd_span.finish()
            raise
