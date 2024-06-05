import pytest

from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsAnthropic:
    def test_completion(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
        """Ensure llmobs records are emitted for completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_multi_prompt.yaml"):
            llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                system="Respond only in all caps.",
                temperature=0.8,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Hello, I am looking for information about some books!"},
                            {"type": "text", "text": "What is the best selling book?"},
                        ],
                    }
                ],
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": "Respond only in all caps.", "role": "system"},
                    {"content": "Hello, I am looking for information about some books!", "role": "user"},
                    {"content": "What is the best selling book?", "role": "user"},
                ],
                output_messages=[{"content": 'THE BEST-SELLING BOOK OF ALL TIME IS "DON', "role": "assistant"}],
                metadata={"temperature": 0.8, "max_tokens": 15.0},
                token_metrics={"prompt_tokens": 32, "completion_tokens": 15, "total_tokens": 47},
                tags={"ml_app": "<ml-app-name>"},
            )
        )

    def test_error(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an error.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic(api_key="invalid_api_key")
        with request_vcr.use_cassette("anthropic_completion_invalid_api_key.yaml"):
            try:
                llm.messages.create(
                    model="claude-3-opus-20240229",
                    max_tokens=15,
                    system="Respond only in all caps.",
                    temperature=0.8,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Hello, I am looking for information about some books!"},
                                {"type": "text", "text": "What is the best selling book?"},
                            ],
                        }
                    ],
                )
            except Exception:
                pass
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": "Respond only in all caps.", "role": "system"},
                    {"content": "Hello, I am looking for information about some books!", "role": "user"},
                    {"content": "What is the best selling book?", "role": "user"},
                ],
                output_messages=[{"content": ""}],
                error="anthropic.AuthenticationError",
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
                metadata={"temperature": 0.8, "max_tokens": 15.0},
                tags={"ml_app": "<ml-app-name>"},
            )
        )
