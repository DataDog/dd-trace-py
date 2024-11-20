import os

from google.api_core.exceptions import InvalidArgument
import mock
import pytest

from tests.contrib.vertexai.utils import MOCK_COMPLETION_SIMPLE_1
from tests.contrib.vertexai.utils import MOCK_COMPLETION_TOOL
from tests.contrib.vertexai.utils import MOCK_COMPLETION_SIMPLE_2
from tests.contrib.vertexai.utils import MOCK_COMPLETION_STREAM_CHUNKS
from tests.contrib.vertexai.utils import MOCK_COMPLETION_TOOL_CALL_STREAM_CHUNKS
from tests.contrib.vertexai.utils import _async_streamed_response
from tests.contrib.vertexai.utils import _mock_completion_response
from tests.contrib.vertexai.utils import _mock_completion_stream_chunk
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsVertexai:
    def test_completion(self, vertexai, mock_llmobs_writer, mock_tracer):
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
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
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
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)
    
    def test_completion_error(self, vertexai, mock_llmobs_writer, mock_tracer):
        llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
        llm._prediction_client.generate_content = mock.Mock()
        llm._prediction_client.generate_content.side_effect = TypeError("_GenerativeModel.generate_content() got an unexpected keyword argument 'candidate_count'")
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
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
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
        )

    def test_completion_tool(self, vertexai, mock_llmobs_writer, mock_client, mock_tracer):
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
        expected_llmobs_span_event = _expected_llmobs_llm_span_event(
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
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event)

    
