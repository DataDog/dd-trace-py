import pytest

from tests.contrib.google_genai.utils import FULL_GENERATE_CONTENT_CONFIG
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsGoogleGenAI:
    def test_generate_content(self, genai, mock_llmobs_writer, mock_tracer, mock_generate_content):
        client = genai.Client()
        client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))


    def test_generate_content_error(self, genai, mock_llmobs_writer, mock_tracer, mock_generate_content):
        client = genai.Client()
        with pytest.raises(TypeError):
            client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    # def test_generate_content_stream():
        # pass



def expected_llmobs_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "You are a helpful assistant.", "role": "system"},
            {"content": "Why is the sky blue? Explain in 2-3 sentences.", "role": "user"}
        ],
        output_messages=[{"content": "The sky is blue due to rayleigh scattering", "role": "model"}],
        metadata=FULL_GENERATE_CONTENT_CONFIG.model_dump(),
        token_metrics={"input_tokens": 8, "output_tokens": 9, "total_tokens": 17},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )

def expected_llmobs_error_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "You are a helpful assistant.", "role": "system"},
            {"content": "Why is the sky blue? Explain in 2-3 sentences.", "role": "user"}
        ],
        output_messages=[{"content": ""}],
        error="builtins.TypeError",
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
        metadata=FULL_GENERATE_CONTENT_CONFIG.model_dump(),
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )