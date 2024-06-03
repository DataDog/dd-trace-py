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
        with request_vcr.use_cassette("anthropic_hello_world.yaml"):
            llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Reply: 'Hello World!' when I say: 'Hello'",
                            },
                            {
                                "type": "text",
                                "text": "Hello",
                            },
                        ],
                    },
                    {"role": "assistant", "content": "Hello World!"},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Hello",
                            }
                        ],
                    },
                ],
                temperature=0.8,
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": "Reply: 'Hello World!' when I say: 'Hello'", "role": "user"},
                    {"content": "Hello", "role": "user"},
                    {"content": "Hello World!", "role": "assistant"},
                    {"content": "Hello", "role": "user"},
                ],
                output_messages=[{"content": "Hello World!", "role": "assistant"}],
                metadata={"temperature": 0.8, "max_tokens": 15},
                token_metrics={"input_tokens": 33, "output_tokens": 6, "total_tokens": 39},
                tags={"ml_app": "<ml-app-name>"},
            )
        )
