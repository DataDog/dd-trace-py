import pytest

from mock import PropertyMock, patch

from tests.utils import override_global_config
from tests.contrib.vertexai.utils import weather_tool
from tests.contrib.vertexai.utils import _async_streamed_response
from tests.contrib.vertexai.utils import _mock_completion_response
from tests.contrib.vertexai.utils import _mock_completion_stream_chunk
from tests.contrib.vertexai.utils import MOCK_COMPLETION_SIMPLE_1
from tests.contrib.vertexai.utils import MOCK_COMPLETION_SIMPLE_2
from tests.contrib.vertexai.utils import MOCK_COMPLETION_SIMPLE_3
from tests.contrib.vertexai.utils import MOCK_COMPLETION_TOOL
from tests.contrib.vertexai.utils import MOCK_COMPLETION_STREAM_CHUNKS
from tests.contrib.vertexai.utils import MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS

def test_global_tags(vertexai, mock_client, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        with override_global_config(dict(service="test-svc", env="staging", version="1234")):
            llm.generate_content(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "GenerativeModel.generate_content"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("vertexai.request.model") == "gemini-1.5-flash"

@pytest.mark.snapshot
def test_vertexai_completion(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm.generate_content(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            )
        

@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
def test_vertexai_completion_error(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        with pytest.raises(TypeError):
            llm.generate_content(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                candidate_count=2, # candidate_count is not a valid keyword argument 
            )


@pytest.mark.snapshot
def test_vertexai_completion_tool(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        llm.generate_content(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )

@pytest.mark.snapshot
def test_vertexai_completion_multiple_messages(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm.generate_content(
            [
                {"role": "user", "parts": [{"text": "Hello World!"}]},
                {"role": "model", "parts": [{"text": "Great to meet you. What would you like to know?"}]},
                {"role": "user", "parts": [{"text": "Why do bears hibernate?"}]},
            ],
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=0),
        )

@pytest.mark.snapshot
def test_vertexai_completion_system_prompt(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_3))
        llm = vertexai.generative_models.GenerativeModel(
            "gemini-1.5-flash",
            system_instruction=[
                vertexai.generative_models.Part.from_text("You are required to insist that bears do not hibernate.")
            ],
        )
        llm.generate_content(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=50, temperature=0),
        )

@pytest.mark.snapshot
def test_vertexai_completion_stream(vertexai, mock_client):
     with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["stream_generate_content"] = [
            (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        response = llm.generate_content(
            "How big is the solar system?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            stream=True,
        )
        for _ in response:
            pass

@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
def test_vertexai_completion_stream_error(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["stream_generate_content"] = [
            (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        with pytest.raises(TypeError):
            response = llm.generate_content(
                "How big is the solar system?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                stream = True,
                candidate_count=2, # candidate_count is not a valid keyword argument 
            )
            for _ in response:
                pass


@pytest.mark.snapshot
def test_vertexai_completion_stream_tool(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["stream_generate_content"] = [
            (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        response = llm.generate_content(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            stream=True,
        )
        for _ in response:
            pass

@pytest.mark.snapshot
async def test_vertexai_completion_async(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        await llm.generate_content_async(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            )
    
@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
async def test_vertexai_completion_async_error(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        with pytest.raises(TypeError):
            await llm.generate_content_async(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=0),
                candidate_count=2, # candidate_count is not a valid keyword argument 
            )

@pytest.mark.snapshot
async def test_vertexai_completion_async_tool(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        await llm.generate_content_async(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=0),
        )

@pytest.mark.snapshot
async def test_vertexai_completion_async_stream(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["stream_generate_content"] = [
            _async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        response = await llm.generate_content_async(
            "How big is the solar system?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            stream=True,
        )
        async for _ in response:
            pass

@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
async def test_vertexai_completion_async_stream_error(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["stream_generate_content"] = [
            _async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        with pytest.raises(TypeError):
            response = await llm.generate_content_async(
                "How big is the solar system?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                stream=True,
                candidate_count = 2,
            )
            async for _ in response:
                pass

@pytest.mark.snapshot
async def test_vertexai_completion_async_stream_tool(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["stream_generate_content"] = [
            _async_streamed_response(MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        response = await llm.generate_content_async(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=0),
            stream=True,
        )
        async for _ in response:
            pass

@pytest.mark.snapshot
def test_vertexai_chat(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        chat.send_message(
            "Why do bears hibernate?", generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )

@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
def test_vertexai_chat_error(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        with pytest.raises(TypeError):
            chat.send_message(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                candidate_count=2, # candidate_count is not a valid keyword argument 
            )

@pytest.mark.snapshot
def test_vertexai_chat_tool(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        chat.send_message(
            "What is the weather like in New York City?", generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )

@pytest.mark.snapshot
def test_vertexai_chat_system_prompt(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_3))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", system_instruction=
            [
                vertexai.generative_models.Part.from_text("You are required to insist that bears do not hibernate.")
            ],
        )
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        chat.send_message(
            "Why do bears hibernate?", generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )

@pytest.mark.snapshot
def test_vertexai_chat_stream(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["stream_generate_content"] = [
            (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        response = chat.send_message(
            "How big is the solar system?", 
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            stream=True,
        )
        for _ in response:
            pass

@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
def test_vertexai_chat_stream_error(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["stream_generate_content"] = [
            (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        with pytest.raises(TypeError):
            response = chat.send_message(
                "How big is the solar system?", 
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                stream=True,
                candidate_count=2,
            )
            for _ in response:
                pass

@pytest.mark.snapshot
def test_vertexai_chat_stream_tool(vertexai, mock_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_client
        mock_client.responses["stream_generate_content"] = [
            (_mock_completion_stream_chunk(chunk) for chunk in MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        response = chat.send_message(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            stream=True,
        )
        for _ in response:
            pass

@pytest.mark.snapshot
async def test_vertexai_chat_async(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        await chat.send_message_async(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            )

@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
async def test_vertexai_chat_async_error(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_2))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        with pytest.raises(TypeError):
            await chat.send_message_async(
                    "Why do bears hibernate?",
                    generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                    candidate_count = 2,
                )

@pytest.mark.snapshot
async def test_vertexai_chat_async_tool(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        await chat.send_message_async(
                "What is the weather like in New York City?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            )

@pytest.mark.snapshot
async def test_vertexai_chat_async_stream(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["stream_generate_content"]= [_async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        response = await chat.send_message_async(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                stream = True,
            )
        async for _ in response:
            pass

@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
async def test_vertexai_chat_async_stream_error(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["stream_generate_content"]= [_async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        with pytest.raises(TypeError):
            response = await chat.send_message_async(
                    "Why do bears hibernate?",
                    generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
                    stream = True,
                    candidate_count = 2,
                )
            async for _ in response:
                pass

@pytest.mark.snapshot
async def test_vertexai_chat_async_stream_tool(vertexai, mock_async_client):
    with patch.object(vertexai.generative_models.GenerativeModel, '_prediction_async_client', new_callable=PropertyMock) as mock_client_property:
        mock_client_property.return_value = mock_async_client
        mock_async_client.responses["stream_generate_content"] = [
            _async_streamed_response(MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS)
        ]
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(role="user", parts=[vertexai.generative_models.Part.from_text("Hello world!")]),
                vertexai.generative_models.Content(role="model", parts=[vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")]),
            ]
        )
        response = await chat.send_message_async(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
            stream = True,
        )
        async for _ in response:
            pass