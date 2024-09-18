import collections

from google.generativeai import protos
import mock


MOCK_COMPLETION_SIMPLE_1 = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": "The argument for LeBron James being the 'Greatest of All Time' ("
                        "GOAT) is multifaceted and involves a variety of factors. Here's a "
                        "breakdown"
                    }
                ],
                "role": "model",
            },
            "finish_reason": 2,
        }
    ],
    "usage_metadata": {"prompt_token_count": 12, "candidates_token_count": 30, "total_token_count": 42},
}
MOCK_COMPLETION_SIMPLE_2 = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": "The sky appears blue due to a phenomenon called **Rayleigh "
                        "scattering**. \nHere's how it works:* **Sunlight is made up of "
                        "all colors of the"
                    }
                ],
                "role": "model",
            },
            "finish_reason": 2,
        }
    ],
    "usage_metadata": {"prompt_token_count": 24, "candidates_token_count": 35, "total_token_count": 59},
}
MOCK_COMPLETION_SIMPLE_SYSTEM = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": "Look, I respect LeBron James. He's a phenomenal player, "
                        "an incredible athlete, and a great ambassador for the game. But "
                        "when it comes to the GOAT, the crown belongs to His Airness, "
                        "Michael Jordan!"
                    }
                ],
                "role": "model",
            },
            "finish_reason": 2,
        }
    ],
    "usage_metadata": {"prompt_token_count": 29, "candidates_token_count": 45, "total_token_count": 74},
}
MOCK_COMPLETION_STREAM_CHUNKS = (
    {"text": "A", "usage_metadata": {"prompt_token_count": 6, "candidates_token_count": 1, "total_token_count": 7}},
    {
        "text": ", B, C, D, E, F, G, H, I",
        "usage_metadata": {"prompt_token_count": 6, "candidates_token_count": 17, "total_token_count": 23},
    },
    {
        "text": ", J, K, L, M, N, O, P, Q",
        "usage_metadata": {"prompt_token_count": 6, "candidates_token_count": 33, "total_token_count": 39},
    },
    {
        "text": ", R, S, T, U, V, W, X, Y, Z.\n",
        "usage_metadata": {"prompt_token_count": 6, "candidates_token_count": 52, "total_token_count": 58},
    },
)
MOCK_COMPLETION_TOOL_CALL = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "function_call": {
                            "name": "set_light_values",
                            "args": {
                                "fields": [{"key": "color_temp", "value": "warm"}, {"key": "brightness", "value": 50}]
                            },
                        }
                    }
                ],
                "role": "model",
            },
            "finish_reason": 2,
        }
    ],
    "usage_metadata": {"prompt_token_count": 150, "candidates_token_count": 25, "total_token_count": 175},
}
MOCK_CHAT_COMPLETION_TOOL_RESPONSE = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {"text": "OK. I've dimmed the lights to 50% and set the color temperature to warm.  How's that? \n"}
                ],
                "role": "model",
            },
            "finish_reason": 2,
        },
    ],
    "usage_metadata": {"prompt_token_count": 206, "candidates_token_count": 27, "total_token_count": 233},
}
MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS = (
    {
        "function_call": {
            "name": "set_light_values",
            "args": {"fields": [{"key": "color_temp", "value": "warm"}, {"key": "brightness", "value": 50}]},
        },
        "usage_metadata": {"prompt_token_count": 150, "candidates_token_count": 25, "total_token_count": 175},
    },
)
MOCK_COMPLETION_IMG_CALL = {
    "candidates": [{"content": {"parts": [{"text": "57 100 900 911"}], "role": "model"}, "finish_reason": 2}],
    "usage_metadata": {"prompt_token_count": 277, "candidates_token_count": 14, "total_token_count": 291},
}


class MockGenerativeModelClient:
    def __init__(self):
        self.responses = collections.defaultdict(list)
        self._client_options = mock.Mock()
        self._client_options.api_key = "<not-a-real-key>"

    def generate_content(self, request, **kwargs):
        return self.responses["generate_content"].pop(0)

    def stream_generate_content(self, request, **kwargs):
        return self.responses["stream_generate_content"].pop(0)


class MockGenerativeModelAsyncClient:
    def __init__(self):
        self.responses = collections.defaultdict(list)
        self._client = mock.Mock()
        self._client_options = mock.Mock()
        self._client._client_options = self._client_options
        self._client_options.api_key = "<not-a-real-key>"

    async def generate_content(self, request, **kwargs):
        return self.responses["generate_content"].pop(0)

    async def stream_generate_content(self, request, **kwargs):
        return self.responses["stream_generate_content"].pop(0)


def set_light_values(brightness, color_temp):
    """Set the brightness and color temperature of a room light. (mock API).
    Args:
        brightness: Light level from 0 to 100. Zero is off and 100 is full brightness
        color_temp: Color temperature of the light fixture, which can be `daylight`, `cool` or `warm`.
    Returns:
        A dictionary containing the set brightness and color temperature.
    """
    return {"brightness": brightness, "colorTemperature": color_temp}


async def _async_streamed_response(mock_chunks):
    """Return async streamed response chunks to be processed by the mock async client."""
    for chunk in mock_chunks:
        yield _mock_completion_stream_chunk(chunk)


def _mock_completion_response(mock_completion_dict):
    mock_content = protos.Content(mock_completion_dict["candidates"][0]["content"])
    return protos.GenerateContentResponse(
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
        mock_content = protos.Content({"parts": [{"text": chunk["text"]}], "role": "model"})
    elif chunk.get("function_call"):
        mock_content = protos.Content({"parts": [{"function_call": chunk["function_call"]}], "role": "model"})
    return protos.GenerateContentResponse(
        {"candidates": [{"content": mock_content, "finish_reason": 2}], "usage_metadata": chunk["usage_metadata"]}
    )
