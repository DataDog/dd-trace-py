import sys


# https://cloud.google.com/vertex-ai/generative-ai/docs/model-garden/quickstart
# for vertex, it seems like the best way to associate provider name with each call is based on the model name prefix
MODEL_PREFIX_TO_PROVIDER = {
    "gemini": "google",
    "imagen": "google",
    "veo": "google",
    "jamba": "ai21labs",
    "claude": "anthropic",
    "llama": "meta",
    "mistral": "mistral",
}


def extract_provider_and_model_name(kwargs):
    model_name = kwargs.get("model", "").split("/")[-1]
    provider_name = "custom"
    for prefix in MODEL_PREFIX_TO_PROVIDER.keys():
        if model_name.startswith(prefix):
            provider_name = MODEL_PREFIX_TO_PROVIDER[prefix]
    return provider_name, model_name if len(model_name) > 0 else "unknown"


class BaseTracedGoogleGenAIStreamResponse:
    def __init__(self, generation_response, integration, span, args, kwargs):
        self._generation_response = generation_response
        self._integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs
        self._chunks = []


class TracedGoogleGenAIStreamResponse(BaseTracedGoogleGenAIStreamResponse):
    # generate_content_stream returns Iterator[GenerateContentResponse]
    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = self._generation_response.__next__()
            self._chunks.append(chunk)
            return chunk
        except StopIteration:
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
