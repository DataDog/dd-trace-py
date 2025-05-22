import mock
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
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsVertexai:
    def test_completion(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm.generate_content(
            contents="Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    def test_completion_error(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.generate_content = mock.Mock()
        llm._prediction_client.generate_content.side_effect = TypeError(
            "_GenerativeModel.generate_content() got an unexpected keyword argument 'candidate_count'"
        )
        with pytest.raises(TypeError):
            llm.generate_content(
                "Why do bears hibernate?",
                generation_config=vertexai.generative_models.GenerationConfig(
                    stop_sequences=["x"], max_output_tokens=30, temperature=1.0
                ),
                candidate_count=2,  # candidate_count is not a valid keyword argument
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    def test_completion_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
        llm.generate_content(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))

    def test_completion_multiple_messages(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_history_span_event(span))

    def test_completion_system_prompt(self, vertexai, mock_llmobs_writer, mock_tracer):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_system_prompt_span_event(span))

    def test_completion_model_generation_config(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm.generate_content(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    def test_completion_no_generation_config(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm.generate_content(
            "Why do bears hibernate?",
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_no_generation_config_span_event(span))

    def test_completion_stream(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_span_event(span))

    def test_completion_stream_error(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_error_span_event(span))

    def test_completion_stream_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))

    async def test_completion_async(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    async def test_completion_async_error(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    async def test_completion_async_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        llm._prediction_async_client.responses["generate_content"].append(
            _mock_completion_response(MOCK_COMPLETION_TOOL)
        )
        await llm.generate_content_async(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))

    async def test_completion_async_stream(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_span_event(span))

    async def test_completion_async_stream_error(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_error_span_event(span))

    async def test_completion_async_stream_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))

    def test_chat(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        chat = llm.start_chat()
        chat.send_message(
            content="Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    def test_chat_history(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        chat = llm.start_chat(
            history=[
                vertexai.generative_models.Content(
                    role="user", parts=[vertexai.generative_models.Part.from_text("Hello World!")]
                ),
                vertexai.generative_models.Content(
                    role="model",
                    parts=[
                        vertexai.generative_models.Part.from_text("Great to meet you. What would you like to know?")
                    ],
                ),
            ]
        )
        chat.send_message(
            vertexai.generative_models.Part.from_text("Why do bears hibernate?"),
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_history_span_event(span))

    def test_vertexai_chat_error(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    def test_chat_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL))
        chat = llm.start_chat()
        chat.send_message(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))

    def test_chat_system_prompt(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_system_prompt_span_event(span))

    def test_chat_stream(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_span_event(span))

    def test_chat_stream_error(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_error_span_event(span))

    def test_chat_stream_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))

    async def test_chat_async(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    async def test_chat_async_error(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    async def test_chat_async_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
        llm._prediction_async_client.responses["generate_content"].append(
            _mock_completion_response(MOCK_COMPLETION_TOOL)
        )
        chat = llm.start_chat()
        await chat.send_message_async(
            "What is the weather like in New York City?",
            generation_config=vertexai.generative_models.GenerationConfig(
                stop_sequences=["x"], max_output_tokens=30, temperature=1.0
            ),
        )

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))

    async def test_chat_async_stream(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_span_event(span))

    async def test_chat_async_stream_error(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_stream_error_span_event(span))

    async def test_chat_async_stream_tool(self, vertexai, mock_llmobs_writer, mock_tracer):
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

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_tool_span_event(span))


def expected_llmobs_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[{"content": "Why do bears hibernate?"}],
        output_messages=[
            {"content": MOCK_COMPLETION_SIMPLE_1["candidates"][0]["content"]["parts"][0]["text"], "role": "model"},
        ],
        metadata={"temperature": 1.0, "max_output_tokens": 30},
        token_metrics={"input_tokens": 14, "output_tokens": 16, "total_tokens": 30},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )


def expected_llmobs_error_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[{"content": "Why do bears hibernate?"}],
        output_messages=[{"content": ""}],
        error="builtins.TypeError",
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
        metadata={"temperature": 1.0, "max_output_tokens": 30},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )


def expected_llmobs_tool_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[{"content": "What is the weather like in New York City?"}],
        output_messages=[
            {
                "content": "",
                "role": "model",
                "tool_calls": [
                    {
                        "name": "get_current_weather",
                        "arguments": {
                            "location": "New York City, NY",
                        },
                    }
                ],
            }
        ],
        metadata={"temperature": 1.0, "max_output_tokens": 30},
        token_metrics={"input_tokens": 43, "output_tokens": 11, "total_tokens": 54},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )


def expected_llmobs_stream_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[{"content": "How big is the solar system?"}],
        output_messages=[
            {"content": "".join([chunk["text"] for chunk in MOCK_COMPLETION_STREAM_CHUNKS]), "role": "model"},
        ],
        metadata={"temperature": 1.0, "max_output_tokens": 30},
        token_metrics={"input_tokens": 16, "output_tokens": 37, "total_tokens": 53},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )


def expected_llmobs_stream_error_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[{"content": "How big is the solar system?"}],
        output_messages=[{"content": ""}],
        error="builtins.TypeError",
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
        metadata={"temperature": 1.0, "max_output_tokens": 30},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )


def expected_llmobs_history_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[
            {"content": "Hello World!", "role": "user"},
            {"content": "Great to meet you. What would you like to know?", "role": "model"},
            {"content": "Why do bears hibernate?"},
        ],
        output_messages=[
            {"content": MOCK_COMPLETION_SIMPLE_1["candidates"][0]["content"]["parts"][0]["text"], "role": "model"},
        ],
        metadata={"temperature": 1.0, "max_output_tokens": 30},
        token_metrics={"input_tokens": 14, "output_tokens": 16, "total_tokens": 30},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )


def expected_llmobs_system_prompt_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[
            {"content": "You are required to insist that bears do not hibernate.", "role": "system"},
            {"content": "Why do bears hibernate?"},
        ],
        output_messages=[
            {"content": MOCK_COMPLETION_SIMPLE_2["candidates"][0]["content"]["parts"][0]["text"], "role": "model"},
        ],
        metadata={"temperature": 1.0, "max_output_tokens": 50},
        token_metrics={"input_tokens": 16, "output_tokens": 50, "total_tokens": 66},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )


def expected_llmobs_no_generation_config_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-1.5-flash",
        model_provider="google",
        input_messages=[{"content": "Why do bears hibernate?"}],
        output_messages=[
            {"content": MOCK_COMPLETION_SIMPLE_1["candidates"][0]["content"]["parts"][0]["text"], "role": "model"},
        ],
        metadata={},
        token_metrics={"input_tokens": 14, "output_tokens": 16, "total_tokens": 30},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vertexai"},
    )
