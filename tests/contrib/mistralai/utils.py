from ddtrace.llmobs._integrations.mistralai import EMBED_METADATA_PARAMS
from ddtrace.llmobs._integrations.mistralai import GENERATE_METADATA_PARAMS


CHAT_COMPLETION_BODY = {
    "id": "chatcomplete-305",
    "object": "chat.completion",
    "model": "mistral-large-latest",
    "created": 1,
    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "The sky is blue due to Rayleigh scattering.",
            },
        }
    ],
}

TOOL_CALL_BODY = {
    "id": "chatcomplete-tool305",
    "object": "chat.completion",
    "model": "mistral-large-latest",
    "created": 1,
    "usage": {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_305",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "New York, NY"}',
                        },
                    }
                ],
            },
        }
    ],
}

EMBEDDING_BODY = {
    "id": "emb-305",
    "object": "list",
    "model": "mistral-embed",
    "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
    "data": [
        {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3, 0.4, 0.5]},
    ],
}

TOOL_FINAL_RESPONSE_BODY = {
    "id": "chatcomplete-final305",
    "object": "chat.completion",
    "model": "mistral-large-latest",
    "created": 1,
    "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "The weather in New York, NY is currently 72°F and sunny.",
            },
        }
    ],
}


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

# Full set of supported embed kwargs
FULL_EMBED_REQUEST_KWARGS = {
    "encoding_format": "float",
}


def get_expected_chat_metadata():
    metadata = {}
    for param in GENERATE_METADATA_PARAMS:
        value = FULL_CHAT_REQUEST_KWARGS.get(param)
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
