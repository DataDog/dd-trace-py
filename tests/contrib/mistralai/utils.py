from ddtrace.llmobs._integrations.mistralai import EMBED_METADATA_PARAMS
from ddtrace.llmobs._integrations.mistralai import GENERATE_METADATA_PARAMS


def get_weather(location: str) -> dict:
    """Mock weather function for tool testing."""
    return {"location": location, "temperature": 72, "unit": "fahrenheit", "forecast": "Sunny"}


CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }
]

# Full set of supported chat kwargs
FULL_CHAT_REQUEST_KWARGS = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 100,
    "random_seed": 42,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
}

REASONING_CHAT_REQUEST_KWARGS = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 500,
    "random_seed": 42,
}

# Full set of supported embed kwargs
FULL_EMBED_REQUEST_KWARGS = {
    "encoding_format": "float",
}


def get_expected_chat_metadata(request_kwargs=None):
    request_kwargs = request_kwargs if request_kwargs is not None else FULL_CHAT_REQUEST_KWARGS
    metadata = {}
    for param in GENERATE_METADATA_PARAMS:
        value = request_kwargs.get(param)
        if value is not None:
            metadata[param] = value
    return metadata


def get_expected_embed_metadata():
    metadata = {}
    for param in EMBED_METADATA_PARAMS:
        value = FULL_EMBED_REQUEST_KWARGS.get(param)
        if value is not None:
            metadata[param] = value
    return metadata
