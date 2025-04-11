import pytest

from tests.contrib.vertexai.utils import MOCK_COMPLETION_SIMPLE_1
from tests.contrib.vertexai.utils import MOCK_COMPLETION_SIMPLE_2
from tests.contrib.vertexai.utils import MOCK_COMPLETION_STREAM_CHUNKS
from tests.contrib.vertexai.utils import MOCK_COMPLETION_TOOL
from tests.contrib.vertexai.utils import MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS
from tests.contrib.vertexai.utils import _async_streamed_response
from tests.contrib.vertexai.utils import _mock_completion_response
from tests.contrib.vertexai.utils import _mock_completion_stream_chunk
from tests.contrib.vertexai.utils import weather_tool
from tests.utils import override_global_config


def test_global_tags(vertexai, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        llm.generate_content(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "GenerativeModel.generate_content"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("vertexai.request.model") == "gemini-1.5-flash"


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion")
def test_vertexai_completion(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    llm.generate_content(
        contents="Why do bears hibernate?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_error",
    ignores=["meta.error.stack", "meta.error.message"],
)
def test_vertexai_completion_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    with pytest.raises(TypeError):
        llm.generate_content(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            candidate_count=2,  # candidate_count is not a valid keyword argument
        )


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_tool")
def test_vertexai_completion_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
    llm.generate_content(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_multiple_messages")
def test_vertexai_completion_multiple_messages(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    llm.generate_content(
        [
            {"role": "user", "parts": [{"text": "Hello World!"}]},
            {"role": "model", "parts": [{"text": "Great to meet you. What would you like to know?"}]},
            {"parts": [{"text": "Why do bears hibernate?"}]},
        ],
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_system_prompt")
def test_vertexai_completion_system_prompt(vertexai):
    llm = vertexai.generative_models.GenerativeModel(
        "gemini-1.5-flash",
        system_instruction=[
            vertexai.generative_models.Part.from_text("You are required to insist that bears do not hibernate.")
        ],
    )
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
    llm.generate_content(
        "Why do bears hibernate?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=50, temperature=1.0
        ),
    )


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream")
def test_vertexai_completion_stream(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    response = llm.generate_content(
        contents="How big is the solar system?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    for _ in response:
        pass


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_error",
    ignores=["meta.error.stack", "meta.error.message"],
)
def test_vertexai_completion_stream_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    with pytest.raises(TypeError):
        response = llm.generate_content(
            "How big is the solar system?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            stream=True,
            candidate_count=2,  # candidate_count is not a valid keyword argument
        )
        for _ in response:
            pass


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_tool")
def test_vertexai_completion_stream_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
    ]
    response = llm.generate_content(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    for _ in response:
        pass


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion", ignores=["resource"])
async def test_vertexai_completion_async(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_async_client.responses["generate_content"].append(
        _mock_completion_response(MOCK_COMPLETION_SIMPLE_1)
    )
    await llm.generate_content_async(
        "Why do bears hibernate?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_error",
    ignores=["meta.error.stack", "meta.error.message", "resource"],
)
async def test_vertexai_completion_async_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_async_client.responses["generate_content"].append(
        _mock_completion_response(MOCK_COMPLETION_SIMPLE_1)
    )
    with pytest.raises(TypeError):
        await llm.generate_content_async(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            candidate_count=2,  # candidate_count is not a valid keyword argument
        )


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_tool", ignores=["resource"])
async def test_vertexai_completion_async_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
    await llm.generate_content_async(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream", ignores=["resource"]
)
async def test_vertexai_completion_async_stream(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_async_client.responses["stream_generate_content"] = [
        _async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    response = await llm.generate_content_async(
        "How big is the solar system?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    async for _ in response:
        pass


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_error",
    ignores=["meta.error.stack", "meta.error.message", "resource"],
)
async def test_vertexai_completion_async_stream_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_async_client.responses["stream_generate_content"] = [
        _async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    with pytest.raises(TypeError):
        response = await llm.generate_content_async(
            "How big is the solar system?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            stream=True,
            candidate_count=2,
        )
        async for _ in response:
            pass


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_tool", ignores=["resource"]
)
async def test_vertexai_completion_async_stream_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_async_client.responses["stream_generate_content"] = [
        _async_streamed_response(MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
    ]
    response = await llm.generate_content_async(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    async for _ in response:
        pass


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion", ignores=["resource"])
def test_vertexai_chat(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    chat = llm.start_chat()
    chat.send_message(
        content="Why do bears hibernate?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_multiple_messages", ignores=["resource"]
)
def test_vertexai_chat_history(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    chat = llm.start_chat(
        history=[
            vertexai.generative_models.Content(
                role="user", parts=[vertexai.generative_models.Part.from_text("Hello World!")]
            ),
            vertexai.generative_models.Content(
                role="model",
                parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")],
            ),
        ]
    )
    chat.send_message(
        vertexai.generative_models.Part.from_text("Why do bears hibernate?"),
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_error",
    ignores=["resource", "meta.error.stack", "meta.error.message"],
)
def test_vertexai_chat_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
    chat = llm.start_chat()
    with pytest.raises(TypeError):
        chat.send_message(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            candidate_count=2,  # candidate_count is not a valid keyword argument
        )


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_tool", ignores=["resource"])
def test_vertexai_chat_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
    chat = llm.start_chat()
    chat.send_message(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_system_prompt", ignores=["resource"]
)
def test_vertexai_chat_system_prompt(vertexai):
    llm = vertexai.generative_models.GenerativeModel(
        "gemini-1.5-flash",
        system_instruction=[
            vertexai.generative_models.Part.from_text("You are required to insist that bears do not hibernate.")
        ],
    )
    llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
    chat = llm.start_chat()
    chat.send_message(
        "Why do bears hibernate?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=50, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream", ignores=["resource"]
)
def test_vertexai_chat_stream(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    chat = llm.start_chat()
    response = chat.send_message(
        content="How big is the solar system?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    for _ in response:
        pass


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_error",
    ignores=["resource", "meta.error.stack", "meta.error.message"],
)
def test_vertexai_chat_stream_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    chat = llm.start_chat()
    with pytest.raises(TypeError):
        response = chat.send_message(
            "How big is the solar system?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            stream=True,
            candidate_count=2,
        )
        for _ in response:
            pass


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_tool", ignores=["resource"]
)
def test_vertexai_chat_stream_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_client.responses["stream_generate_content"] = [
        (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
    ]
    chat = llm.start_chat()
    response = chat.send_message(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    for _ in response:
        pass


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion", ignores=["resource"])
async def test_vertexai_chat_async(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_async_client.responses["generate_content"].append(
        _mock_completion_response(MOCK_COMPLETION_SIMPLE_1)
    )
    chat = llm.start_chat()
    await chat.send_message_async(
        "Why do bears hibernate?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_error",
    ignores=["resource", "meta.error.stack", "meta.error.message"],
)
async def test_vertexai_chat_async_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_async_client.responses["generate_content"].append(
        _mock_completion_response(MOCK_COMPLETION_SIMPLE_1)
    )
    chat = llm.start_chat()
    with pytest.raises(TypeError):
        await chat.send_message_async(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            candidate_count=2,
        )


@pytest.mark.snapshot(token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_tool", ignores=["resource"])
async def test_vertexai_chat_async_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm._prediction_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
    chat = llm.start_chat()
    await chat.send_message_async(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
    )


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream", ignores=["resource"]
)
async def test_vertexai_chat_async_stream(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_async_client.responses["stream_generate_content"] = [
        _async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    chat = llm.start_chat()
    response = await chat.send_message_async(
        "How big is the solar system?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    async for _ in response:
        pass


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_error",
    ignores=["resource", "meta.error.stack", "meta.error.message"],
)
async def test_vertexai_chat_async_stream_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_async_client.responses["stream_generate_content"] = [
        _async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)
    ]
    chat = llm.start_chat()
    with pytest.raises(TypeError):
        response = await chat.send_message_async(
            "How big is the solar system?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
            stream=True,
            candidate_count=2,
        )
        async for _ in response:
            pass


@pytest.mark.snapshot(
    token="tests.contrib.vertexai.test_vertexai.test_vertexai_completion_stream_tool", ignores=["resource"]
)
async def test_vertexai_chat_async_stream_tool(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm._prediction_async_client.responses["stream_generate_content"] = [
        _async_streamed_response(MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
    ]
    chat = llm.start_chat()
    response = await chat.send_message_async(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(
            stop_sequences=["x"], max_output_tokens=30, temperature=1.0
        ),
        stream=True,
    )
    async for _ in response:
        pass
