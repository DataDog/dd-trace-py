import sys

import wrapt


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
    def __init__(self, wrapped, span):
        super().__init__(wrapped)
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
