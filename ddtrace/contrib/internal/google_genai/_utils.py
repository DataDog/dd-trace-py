import sys


# https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-partner-models
# GeminiAPI: only exports google provided models
# VertexAI: can map provided models to provider based on prefix, but not a complete list
MODEL_PREFIX_TO_PROVIDER = {
    "gemini": "google",
    "imagen": "google",
    "veo": "google",
    "jamba": "ai21labs",
    "claude": "anthropic",
    "llama": "meta",
    "mistral": "mistral",
    "codestral": "mistral",
    "deepseek": "deepseek",
}


def extract_provider_and_model_name(kwargs):
    model_name = kwargs.get("model", "").split("/")[-1]
    provider_name = "custom"
    for prefix in MODEL_PREFIX_TO_PROVIDER.keys():
        if model_name.startswith(prefix):
            provider_name = MODEL_PREFIX_TO_PROVIDER[prefix]
    return provider_name, model_name if len(model_name) > 0 else "unknown"


import wrapt

class BaseTracedGoogleGenAIStreamResponse(wrapt.ObjectProxy):
    def __init__(self, generation_response, span):
        super().__init__(generation_response)
        self._self_dd_span = span
        self._self_chunks = []


class TracedGoogleGenAIStreamResponse(BaseTracedGoogleGenAIStreamResponse):
    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            self._self_chunks.append(chunk)
            return chunk
        except StopIteration:
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
            self._self_dd_span.finish()
            raise
        except Exception:
            self._self_dd_span.set_exc_info(*sys.exc_info())
            self._self_dd_span.finish()
            raise