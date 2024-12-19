import collections

from google.cloud import aiplatform_v1
from vertexai.generative_models import FunctionDeclaration
from vertexai.generative_models import Tool


get_current_weather_func = FunctionDeclaration(
    name="get_current_weather",
    description="Get the current weather in a given location",
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"},
            "unit": {
                "type": "string",
                "enum": [
                    "celsius",
                    "fahrenheit",
                ],
            },
        },
        "required": ["location"],
    },
)

weather_tool = Tool(
    function_declarations=[get_current_weather_func],
)

MOCK_COMPLETION_SIMPLE_1 = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": "Bears hibernate to conserve energy and survive during "
                        "winter months when food is scarce.\n"
                    }
                ],
                "role": "model",
            },
            "finish_reason": "STOP",
        }
    ],
    "usage_metadata": {"prompt_token_count": 14, "candidates_token_count": 16, "total_token_count": 30},
}

MOCK_COMPLETION_SIMPLE_2 = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": "Bears do not hibernate! They enter a state of **deep sleep** "
                        "during the winter months, but it's not true hibernation. \\n\\nHere's..."
                    }
                ],
                "role": "model",
            },
            "finish_reason": "STOP",
        }
    ],
    "usage_metadata": {"prompt_token_count": 16, "candidates_token_count": 50, "total_token_count": 66},
}

MOCK_COMPLETION_TOOL = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "name": "get_current_weather",
                            "args": {
                                "location": "New York City, NY",
                            },
                        }
                    }
                ],
            },
            "finish_reason": "STOP",
        },
    ],
    "usage_metadata": {
        "prompt_token_count": 43,
        "candidates_token_count": 11,
        "total_token_count": 54,
    },
}

MOCK_COMPLETION_STREAM_CHUNKS = (
    {"text": "The"},
    {
        "text": " solar system's size is vast, extending from the Sun to the outer reaches",
    },
    {
        "text": " of the Oort cloud, a distance of roughly 1 to 2 light",
    },
    {
        "text": "-years.\n",
        "finish_reason": "STOP",
        "usage_metadata": {"prompt_token_count": 16, "candidates_token_count": 37, "total_token_count": 53},
    },
)

MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS = (
    {
        "function_call": {
            "name": "get_current_weather",
            "args": {"location": "New York City, NY"},
        },
        "finish_reason": "STOP",
        "usage_metadata": {"prompt_token_count": 43, "candidates_token_count": 11, "total_token_count": 54},
    },
)


async def _async_streamed_response(mock_chunks):
    """Return async streamed response chunks to be processed by the mock async client."""
    for chunk in mock_chunks:
        yield _mock_completion_stream_chunk(chunk)


def _mock_completion_response(mock_completion_dict):
    mock_content = aiplatform_v1.Content(mock_completion_dict["candidates"][0]["content"])
    return aiplatform_v1.GenerateContentResponse(
        {
            "candidates": [
                {"content": mock_content, "finish_reason": mock_completion_dict["candidates"][0]["finish_reason"]}
            ],
            "usage_metadata": mock_completion_dict["usage_metadata"],
        }
    )


def _mock_completion_stream_chunk(chunk):
    mock_content = None
    if chunk.get("text"):
        mock_content = aiplatform_v1.Content({"parts": [{"text": chunk["text"]}], "role": "model"})
    elif chunk.get("function_call"):
        mock_content = aiplatform_v1.Content({"parts": [{"function_call": chunk["function_call"]}], "role": "model"})
    if chunk.get("finish_reason"):
        return aiplatform_v1.GenerateContentResponse(
            {
                "candidates": [{"content": mock_content, "finish_reason": chunk["finish_reason"]}],
                "usage_metadata": chunk["usage_metadata"],
            }
        )
    return aiplatform_v1.GenerateContentResponse({"candidates": [{"content": mock_content}]})


class MockPredictionServiceClient:
    def __init__(self):
        self.responses = collections.defaultdict(list)

    def generate_content(self, request, **kwargs):
        return self.responses["generate_content"].pop(0)

    def stream_generate_content(self, request, **kwargs):
        return self.responses["stream_generate_content"].pop(0)


class MockAsyncPredictionServiceClient:
    def __init__(self):
        self.responses = collections.defaultdict(list)

    async def generate_content(self, request, **kwargs):
        return self.responses["generate_content"].pop(0)

    async def stream_generate_content(self, request, **kwargs):
        return self.responses["stream_generate_content"].pop(0)
