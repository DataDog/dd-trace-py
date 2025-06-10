import os

from google.api_core.exceptions import InvalidArgument
import mock
from PIL import Image
import pytest

from tests.contrib.google_generativeai.utils import MOCK_CHAT_COMPLETION_TOOL_RESPONSE
from tests.contrib.google_generativeai.utils import MOCK_COMPLETION_IMG_CALL
from tests.contrib.google_generativeai.utils import MOCK_COMPLETION_SIMPLE_1
from tests.contrib.google_generativeai.utils import MOCK_COMPLETION_SIMPLE_2
from tests.contrib.google_generativeai.utils import MOCK_COMPLETION_SIMPLE_SYSTEM
from tests.contrib.google_generativeai.utils import MOCK_COMPLETION_STREAM_CHUNKS
from tests.contrib.google_generativeai.utils import MOCK_COMPLETION_TOOL_CALL
from tests.contrib.google_generativeai.utils import MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS
from tests.contrib.google_generativeai.utils import _async_streamed_response
from tests.contrib.google_generativeai.utils import _mock_completion_response
from tests.contrib.google_generativeai.utils import _mock_completion_stream_chunk
from tests.contrib.google_generativeai.utils import set_light_values
from tests.utils import override_global_config


def test_global_tags(genai, mock_client, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        llm.generate_content(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
        )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "GenerativeModel.generate_content"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("google_generativeai.request.model") == "gemini-1.5-flash"
    assert span.get_tag("google_generativeai.request.api_key") == "...key>"


# ignore the function call arg because it comes in with dict keys in a different order than expected
@pytest.mark.snapshot(ignores=["meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"])
def test_gemini_completion(genai, mock_client):
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    llm.generate_content(
        "What is the argument for LeBron James being the GOAT?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
    )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion",
    ignores=["resource", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"],
)
async def test_gemini_completion_async(genai, mock_client_async):
    mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    await llm.generate_content_async(
        "What is the argument for LeBron James being the GOAT?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
    )


@pytest.mark.snapshot(
    ignores=["meta.error.stack", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"]
)
def test_gemini_completion_error(genai, mock_client):
    llm = genai.GenerativeModel("gemini-1.5-flash")
    llm._client = mock.Mock()
    llm._client.generate_content.side_effect = InvalidArgument("Invalid API key. Please pass a valid API key.")
    with pytest.raises(InvalidArgument):
        llm.generate_content(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_error",
    ignores=[
        "resource",
        "meta.error.stack",
        "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args",
    ],
)
async def test_gemini_completion_error_async(genai, mock_client):
    llm = genai.GenerativeModel("gemini-1.5-flash")
    llm._async_client = mock.Mock()
    llm._async_client.generate_content.side_effect = InvalidArgument("Invalid API key. Please pass a valid API key.")
    with pytest.raises(InvalidArgument):
        await llm.generate_content_async(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )


@pytest.mark.snapshot(ignores=["meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"])
def test_gemini_completion_multiple_messages(genai, mock_client):
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    llm.generate_content(
        [
            {"role": "user", "parts": [{"text": "Hello world!"}]},
            {"role": "model", "parts": [{"text": "Great to meet you. What would you like to know?"}]},
            {"role": "user", "parts": [{"text": "Why is the sky blue?"}]},
        ],
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
    )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_multiple_messages",
    ignores=["resource", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"],
)
async def test_gemini_completion_multiple_messages_async(genai, mock_client_async):
    mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    await llm.generate_content_async(
        [
            {"role": "user", "parts": [{"text": "Hello world!"}]},
            {"role": "model", "parts": [{"text": "Great to meet you. What would you like to know?"}]},
            {"role": "user", "parts": [{"text": "Why is the sky blue?"}]},
        ],
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
    )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_multiple_messages",
    ignores=[  # send_message does not include all config options by default
        "meta.google_generativeai.request.generation_config.candidate_count",
        "meta.google_generativeai.request.generation_config.top_k",
        "meta.google_generativeai.request.generation_config.top_p",
        "meta.google_generativeai.request.generation_config.response_mime_type",
        "meta.google_generativeai.request.generation_config.response_schema",
        "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args",
    ],
)
def test_gemini_chat_completion(genai, mock_client):
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    chat = llm.start_chat(
        history=[
            {"role": "user", "parts": "Hello world!"},
            {"role": "model", "parts": "Great to meet you. What would you like to know?"},
        ]
    )
    chat.send_message(
        "Why is the sky blue?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
    )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_multiple_messages",
    ignores=[  # send_message does not include all config options by default
        "resource",
        "meta.google_generativeai.request.generation_config.candidate_count",
        "meta.google_generativeai.request.generation_config.top_k",
        "meta.google_generativeai.request.generation_config.top_p",
        "meta.google_generativeai.request.generation_config.response_mime_type",
        "meta.google_generativeai.request.generation_config.response_schema",
        "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args",
    ],
)
async def test_gemini_chat_completion_async(genai, mock_client_async):
    mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    chat = llm.start_chat(
        history=[
            {"role": "user", "parts": "Hello world!"},
            {"role": "model", "parts": "Great to meet you. What would you like to know?"},
        ]
    )
    await chat.send_message_async(
        "Why is the sky blue?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
    )


@pytest.mark.snapshot(ignores=["meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"])
def test_gemini_completion_system_prompt(genai, mock_client):
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_SYSTEM))
    llm = genai.GenerativeModel(
        "gemini-1.5-flash",
        system_instruction="You are a die-hard Michael Jordan fan that always brings stats to the discussion.",
    )
    llm.generate_content(
        "What is the argument for LeBron James being the GOAT?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=50, temperature=1.0),
    )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_system_prompt",
    ignores=["resource", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"],
)
async def test_gemini_completion_system_prompt_async(genai, mock_client_async):
    mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_SYSTEM))
    llm = genai.GenerativeModel(
        "gemini-1.5-flash",
        system_instruction="You are a die-hard Michael Jordan fan that always brings stats to the discussion.",
    )
    await llm.generate_content_async(
        "What is the argument for LeBron James being the GOAT?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=50, temperature=1.0),
    )


@pytest.mark.snapshot(ignores=["meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"])
def test_gemini_completion_stream(genai, mock_client):
    mock_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    llm = genai.GenerativeModel("gemini-1.5-flash")
    response = llm.generate_content(
        "Can you recite the alphabet?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=60, temperature=1.0),
        stream=True,
    )
    for _ in response:
        pass


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_stream",
    ignores=["resource", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"],
)
async def test_gemini_completion_stream_async(genai, mock_client_async):
    mock_client_async.responses["stream_generate_content"] = [_async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)]
    llm = genai.GenerativeModel("gemini-1.5-flash")
    response = await llm.generate_content_async(
        "Can you recite the alphabet?",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=60, temperature=1.0),
        stream=True,
    )
    async for _ in response:
        pass


@pytest.mark.snapshot(ignores=["meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"])
def test_gemini_tool_completion(genai, mock_client):
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL_CALL))
    llm = genai.GenerativeModel("gemini-1.5-flash", tools=[set_light_values])
    llm.generate_content(
        "Dim the lights so the room feels cozy and warm.",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
    )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_tool_completion",
    ignores=["resource", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"],
)
async def test_gemini_tool_completion_async(genai, mock_client_async):
    mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL_CALL))
    llm = genai.GenerativeModel("gemini-1.5-flash", tools=[set_light_values])
    await llm.generate_content_async(
        "Dim the lights so the room feels cozy and warm.",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
    )


@pytest.mark.snapshot(ignores=["meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"])
def test_gemini_tool_chat_completion(genai, mock_client):
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL_CALL))
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_CHAT_COMPLETION_TOOL_RESPONSE))
    model = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=[set_light_values])
    chat = model.start_chat()
    chat.send_message("Dim the lights so the room feels cozy and warm.")
    response_parts = [
        genai.protos.Part(
            function_response=genai.protos.FunctionResponse(
                name="set_light_values", response={"result": {"brightness": 50, "color_temperature": "warm"}}
            )
        )
    ]
    chat.send_message(response_parts)


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_tool_chat_completion",
    ignores=["resource", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"],
)
async def test_gemini_tool_chat_completion_async(genai, mock_client_async):
    mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL_CALL))
    mock_client_async.responses["generate_content"].append(
        _mock_completion_response(MOCK_CHAT_COMPLETION_TOOL_RESPONSE)
    )
    model = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=[set_light_values])
    chat = model.start_chat()
    await chat.send_message_async("Dim the lights so the room feels cozy and warm.")
    response_parts = [
        genai.protos.Part(
            function_response=genai.protos.FunctionResponse(
                name="set_light_values", response={"result": {"brightness": 50, "color_temperature": "warm"}}
            )
        )
    ]
    await chat.send_message_async(response_parts)


@pytest.mark.snapshot(ignores=["meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"])
def test_gemini_completion_tool_stream(genai, mock_client):
    mock_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
    ]
    llm = genai.GenerativeModel("gemini-1.5-flash", tools=[set_light_values])
    response = llm.generate_content(
        "Dim the lights so the room feels cozy and warm.",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        stream=True,
    )
    for _ in response:
        pass


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_tool_stream",
    ignores=["resource", "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args"],
)
async def test_gemini_completion_tool_stream_async(genai, mock_client_async):
    mock_client_async.responses["stream_generate_content"] = [
        _async_streamed_response(MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
    ]
    llm = genai.GenerativeModel("gemini-1.5-flash", tools=[set_light_values])
    response = await llm.generate_content_async(
        "Dim the lights so the room feels cozy and warm.",
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        stream=True,
    )
    async for _ in response:
        pass


@pytest.mark.snapshot(
    ignores=[
        "meta.google_generativeai.request.contents.0.text",
        "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args",
    ]
)
def test_gemini_completion_image(genai, mock_client):
    """Ensure passing images to generate_content() won't break patching."""
    img = Image.open(os.path.join(os.path.dirname(__file__), "test_data/apple.jpg"))
    mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_IMG_CALL))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    llm.generate_content(
        [img, "Return a bounding box for the apple. \n [ymin, xmin, ymax, xmax]"],
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
    )


@pytest.mark.snapshot(
    token="tests.contrib.google_generativeai.test_google_generativeai.test_gemini_completion_image",
    ignores=[
        "resource",
        "meta.google_generativeai.request.contents.0.text",
        "meta.google_generativeai.response.candidates.0.content.parts.0.function_call.args",
    ],
)
async def test_gemini_completion_image_async(genai, mock_client_async):
    """Ensure passing images to generate_content() won't break patching."""
    img = Image.open(os.path.join(os.path.dirname(__file__), "test_data/apple.jpg"))
    mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_IMG_CALL))
    llm = genai.GenerativeModel("gemini-1.5-flash")
    await llm.generate_content_async(
        [img, "Return a bounding box for the apple. \n [ymin, xmin, ymax, xmax]"],
        generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
    )
