import os

from google.api_core.exceptions import InvalidArgument
import mock
from PIL import Image
import pytest

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
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsGemini:
    def test_completion(self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer):
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = genai.GenerativeModel("gemini-1.5-flash")
        llm.generate_content(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "What is the argument for LeBron James being the GOAT?"}],
            output_messages=[
                {"content": MOCK_COMPLETION_SIMPLE_1["candidates"][0]["content"]["parts"][0]["text"], "role": "model"},
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 35},
            token_metrics={"input_tokens": 12, "output_tokens": 30, "total_tokens": 42},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_completion_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
        mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_1))
        llm = genai.GenerativeModel("gemini-1.5-flash")
        await llm.generate_content_async(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "What is the argument for LeBron James being the GOAT?"}],
            output_messages=[
                {"content": MOCK_COMPLETION_SIMPLE_1["candidates"][0]["content"]["parts"][0]["text"], "role": "model"}
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 35},
            token_metrics={"input_tokens": 12, "output_tokens": 30, "total_tokens": 42},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    def test_completion_error(self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer):
        llm = genai.GenerativeModel("gemini-1.5-flash")
        llm._client = mock.Mock()
        llm._client.generate_content.side_effect = InvalidArgument("Invalid API key. Please pass a valid API key.")
        with pytest.raises(InvalidArgument):
            llm.generate_content(
                "What is the argument for LeBron James being the GOAT?",
                generation_config=genai.types.GenerationConfig(
                    stop_sequences=["x"], max_output_tokens=35, temperature=1.0
                ),
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gemini-1.5-flash",
                model_provider="google",
                input_messages=[{"content": "What is the argument for LeBron James being the GOAT?"}],
                output_messages=[{"content": ""}],
                error="google.api_core.exceptions.InvalidArgument",
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
                metadata={"temperature": 1.0, "max_output_tokens": 35},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
            )
        )

    async def test_completion_error_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
        llm = genai.GenerativeModel("gemini-1.5-flash")
        llm._async_client = mock.Mock()
        llm._async_client.generate_content.side_effect = InvalidArgument(
            "Invalid API key. Please pass a valid API key."
        )
        with pytest.raises(InvalidArgument):
            await llm.generate_content_async(
                "What is the argument for LeBron James being the GOAT?",
                generation_config=genai.types.GenerationConfig(
                    stop_sequences=["x"], max_output_tokens=35, temperature=1.0
                ),
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gemini-1.5-flash",
                model_provider="google",
                input_messages=[{"content": "What is the argument for LeBron James being the GOAT?"}],
                output_messages=[{"content": ""}],
                error="google.api_core.exceptions.InvalidArgument",
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
                metadata={"temperature": 1.0, "max_output_tokens": 35},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
            )
        )

    def test_completion_multiple_messages(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer
    ):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {"content": "Hello world!", "role": "user"},
                {"content": "Great to meet you. What would you like to know?", "role": "model"},
                {"content": "Why is the sky blue?", "role": "user"},
            ],
            output_messages=[
                {"content": MOCK_COMPLETION_SIMPLE_2["candidates"][0]["content"]["parts"][0]["text"], "role": "model"}
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 35},
            token_metrics={"input_tokens": 24, "output_tokens": 35, "total_tokens": 59},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_completion_multiple_messages_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {"content": "Hello world!", "role": "user"},
                {"content": "Great to meet you. What would you like to know?", "role": "model"},
                {"content": "Why is the sky blue?", "role": "user"},
            ],
            output_messages=[
                {"content": MOCK_COMPLETION_SIMPLE_2["candidates"][0]["content"]["parts"][0]["text"], "role": "model"}
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 35},
            token_metrics={"input_tokens": 24, "output_tokens": 35, "total_tokens": 59},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    def test_chat_completion(self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {"content": "Hello world!", "role": "user"},
                {"content": "Great to meet you. What would you like to know?", "role": "model"},
                {"content": "Why is the sky blue?", "role": "user"},
            ],
            output_messages=[
                {"content": MOCK_COMPLETION_SIMPLE_2["candidates"][0]["content"]["parts"][0]["text"], "role": "model"}
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 35},
            token_metrics={"input_tokens": 24, "output_tokens": 35, "total_tokens": 59},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_chat_completion_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {"content": "Hello world!", "role": "user"},
                {"content": "Great to meet you. What would you like to know?", "role": "model"},
                {"content": "Why is the sky blue?", "role": "user"},
            ],
            output_messages=[
                {"content": MOCK_COMPLETION_SIMPLE_2["candidates"][0]["content"]["parts"][0]["text"], "role": "model"}
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 35},
            token_metrics={"input_tokens": 24, "output_tokens": 35, "total_tokens": 59},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    def test_completion_system_prompt(self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer):
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_SYSTEM))
        llm = genai.GenerativeModel(
            "gemini-1.5-flash",
            system_instruction="You are a die-hard Michael Jordan fan that always brings stats to the discussion.",
        )
        llm.generate_content(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=50, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {
                    "content": "You are a die-hard Michael Jordan fan that always brings stats to the discussion.",
                    "role": "system",
                },
                {"content": "What is the argument for LeBron James being the GOAT?"},
            ],
            output_messages=[
                {
                    "content": MOCK_COMPLETION_SIMPLE_SYSTEM["candidates"][0]["content"]["parts"][0]["text"],
                    "role": "model",
                }
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 50},
            token_metrics={"input_tokens": 29, "output_tokens": 45, "total_tokens": 74},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_completion_system_prompt_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
        mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_SIMPLE_SYSTEM))
        llm = genai.GenerativeModel(
            "gemini-1.5-flash",
            system_instruction="You are a die-hard Michael Jordan fan that always brings stats to the discussion.",
        )
        await llm.generate_content_async(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=50, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {
                    "content": "You are a die-hard Michael Jordan fan that always brings stats to the discussion.",
                    "role": "system",
                },
                {"content": "What is the argument for LeBron James being the GOAT?"},
            ],
            output_messages=[
                {
                    "content": MOCK_COMPLETION_SIMPLE_SYSTEM["candidates"][0]["content"]["parts"][0]["text"],
                    "role": "model",
                },
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 50},
            token_metrics={"input_tokens": 29, "output_tokens": 45, "total_tokens": 74},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    def test_completion_stream(self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "Can you recite the alphabet?"}],
            output_messages=[
                {"content": "".join(chunk["text"] for chunk in MOCK_COMPLETION_STREAM_CHUNKS), "role": "model"}
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 60},
            token_metrics={"input_tokens": 6, "output_tokens": 52, "total_tokens": 58},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_completion_stream_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
        mock_client_async.responses["stream_generate_content"] = [
            _async_streamed_response(MOCK_COMPLETION_STREAM_CHUNKS)
        ]
        llm = genai.GenerativeModel("gemini-1.5-flash")
        response = await llm.generate_content_async(
            "Can you recite the alphabet?",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=60, temperature=1.0),
            stream=True,
        )
        async for _ in response:
            pass
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "Can you recite the alphabet?"}],
            output_messages=[
                {"content": "".join(chunk["text"] for chunk in MOCK_COMPLETION_STREAM_CHUNKS), "role": "model"}
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 60},
            token_metrics={"input_tokens": 6, "output_tokens": 52, "total_tokens": 58},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    def test_completion_tool_call(self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer):
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL_CALL))
        llm = genai.GenerativeModel("gemini-1.5-flash", tools=[set_light_values])
        llm.generate_content(
            "Dim the lights so the room feels cozy and warm.",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "Dim the lights so the room feels cozy and warm."}],
            output_messages=[
                {
                    "content": "",
                    "role": "model",
                    "tool_calls": [
                        {
                            "name": "set_light_values",
                            "arguments": {
                                "fields": [{"key": "color_temp", "value": "warm"}, {"key": "brightness", "value": 50}]
                            },
                        }
                    ],
                }
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 30},
            token_metrics={"input_tokens": 150, "output_tokens": 25, "total_tokens": 175},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_completion_tool_call_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
        mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_TOOL_CALL))
        llm = genai.GenerativeModel("gemini-1.5-flash", tools=[set_light_values])
        await llm.generate_content_async(
            "Dim the lights so the room feels cozy and warm.",
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "Dim the lights so the room feels cozy and warm."}],
            output_messages=[
                {
                    "content": "",
                    "role": "model",
                    "tool_calls": [
                        {
                            "name": "set_light_values",
                            "arguments": {
                                "fields": [{"key": "color_temp", "value": "warm"}, {"key": "brightness", "value": 50}]
                            },
                        }
                    ],
                }
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 30},
            token_metrics={"input_tokens": 150, "output_tokens": 25, "total_tokens": 175},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    def test_gemini_completion_tool_stream(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer
    ):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "Dim the lights so the room feels cozy and warm."}],
            output_messages=[
                {
                    "content": "",
                    "role": "model",
                    "tool_calls": [
                        {
                            "name": "set_light_values",
                            "arguments": {
                                "fields": [{"key": "color_temp", "value": "warm"}, {"key": "brightness", "value": 50}]
                            },
                        }
                    ],
                }
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 30},
            token_metrics={"input_tokens": 150, "output_tokens": 25, "total_tokens": 175},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_gemini_completion_tool_stream_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[{"content": "Dim the lights so the room feels cozy and warm."}],
            output_messages=[
                {
                    "content": "",
                    "role": "model",
                    "tool_calls": [
                        {
                            "name": "set_light_values",
                            "arguments": {
                                "fields": [{"key": "color_temp", "value": "warm"}, {"key": "brightness", "value": 50}]
                            },
                        }
                    ],
                }
            ],
            metadata={"temperature": 1.0, "max_output_tokens": 30},
            token_metrics={"input_tokens": 150, "output_tokens": 25, "total_tokens": 175},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    def test_gemini_completion_image(self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client, mock_tracer):
        """Ensure passing images to generate_content() won't break patching."""
        img = Image.open(os.path.join(os.path.dirname(__file__), "test_data/apple.jpg"))
        mock_client.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_IMG_CALL))
        llm = genai.GenerativeModel("gemini-1.5-flash")
        llm.generate_content(
            [img, "Return a bounding box for the apple. \n [ymin, xmin, ymax, xmax]"],
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {"content": "[Non-text content object: {}]".format(repr(img))},
                {"content": "Return a bounding box for the apple. \n [ymin, xmin, ymax, xmax]"},
            ],
            output_messages=[{"content": "57 100 900 911", "role": "model"}],
            metadata={"temperature": 1.0, "max_output_tokens": 30},
            token_metrics={"input_tokens": 277, "output_tokens": 14, "total_tokens": 291},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    async def test_gemini_completion_image_async(
        self, genai, ddtrace_global_config, mock_llmobs_writer, mock_client_async, mock_tracer
    ):
        """Ensure passing images to generate_content() won't break patching."""
        img = Image.open(os.path.join(os.path.dirname(__file__), "test_data/apple.jpg"))
        mock_client_async.responses["generate_content"].append(_mock_completion_response(MOCK_COMPLETION_IMG_CALL))
        llm = genai.GenerativeModel("gemini-1.5-flash")
        await llm.generate_content_async(
            [img, "Return a bounding box for the apple. \n [ymin, xmin, ymax, xmax]"],
            generation_config=genai.types.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=1.0),
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
            span,
            model_name="gemini-1.5-flash",
            model_provider="google",
            input_messages=[
                {"content": "[Non-text content object: {}]".format(repr(img))},
                {"content": "Return a bounding box for the apple. \n [ymin, xmin, ymax, xmax]"},
            ],
            output_messages=[{"content": "57 100 900 911", "role": "model"}],
            metadata={"temperature": 1.0, "max_output_tokens": 30},
            token_metrics={"input_tokens": 277, "output_tokens": 14, "total_tokens": 291},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_generativeai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)
