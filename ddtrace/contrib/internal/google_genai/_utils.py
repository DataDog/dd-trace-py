from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.utils import tag_response_part_google
from ddtrace.llmobs._utils import _get_attr

# https://cloud.google.com/vertex-ai/generative-ai/docs/model-garden/quickstart
# for vertex, it seems like the best way to associate provider name with each call is based on the model name prefix
model_prefix_to_provider = {
    "gemini": "google",
    "imagen": "google",
    "veo": "google",
    "jamba": "ai21labs",
    "claude": "anthropic",
    "llama": "meta",
    "mistral": "mistral",
}

def extract_provider_and_model_name_genai(kwargs):
    model_name = kwargs.get("model", "").split("/")[-1]
    provider_name = "other"
    for prefix in model_prefix_to_provider.keys():
        if model_name.startswith(prefix):
            provider_name = model_prefix_to_provider[prefix]
    return provider_name, model_name if len(model_name) > 0 else "unknown"