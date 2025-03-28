import pytest

from tests.contrib.litellm.utils import async_consume_stream, get_cassette_name, consume_stream, parse_response
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsLiteLLM:
    @pytest.mark.parametrize(
        "stream,n,include_usage",
        [
            (True, 1, True),
            (True, 2, True),
            (False, 1, True),
            (False, 2, True),
            (True, 1, False),
            (True, 2, False),
            (False, 1, False),
            (False, 2, False),
        ],
    )
    def test_completion(self, litellm, request_vcr, mock_llmobs_writer, mock_tracer, stream, n, include_usage):
        with request_vcr.use_cassette(get_cassette_name(stream, n, include_usage)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": include_usage},
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gpt-3.5-turbo",
                model_provider="openai",
                input_messages=messages,
                output_messages=output_messages,
                metadata={"stream": stream, "n": n, "stream_options": {"include_usage": include_usage}},
                token_metrics=token_metrics,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
            )
        )

    @pytest.mark.parametrize(
        "stream,n,include_usage",
        [
            (True, 1, True),
            (True, 2, True),
            (False, 1, True),
            (False, 2, True),
            (True, 1, False),
            (True, 2, False),
            (False, 1, False),
            (False, 2, False),
        ],
    )
    async def test_acompletion(self, litellm, request_vcr, mock_llmobs_writer, mock_tracer, stream, n, include_usage):
        with request_vcr.use_cassette(get_cassette_name(stream, n, include_usage)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": include_usage},
            )
            if stream:
                output_messages, token_metrics = await async_consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gpt-3.5-turbo",
                model_provider="openai",
                input_messages=messages,
                output_messages=output_messages,
                metadata={"stream": stream, "n": n, "stream_options": {"include_usage": include_usage}},
                token_metrics=token_metrics,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
            )
        )

    @pytest.mark.parametrize(
        "stream,n,include_usage",
        [
            (True, 1, True),
            (True, 2, True),
            (False, 1, True),
            (False, 2, True),
            (True, 1, False),
            (True, 2, False),
            (False, 1, False),
            (False, 2, False),
        ],
    )
    def test_text_completion(self, litellm, request_vcr, mock_llmobs_writer, mock_tracer, stream, n, include_usage):
        with request_vcr.use_cassette(get_cassette_name(stream, n, include_usage)):
            prompt = "Hey, what is up?"
            resp = litellm.text_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
                stream_options={"include_usage": include_usage},
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n, is_completion=True)
            else:
                output_messages, token_metrics = parse_response(resp, is_completion=True)

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gpt-3.5-turbo",
                model_provider="openai",
                input_messages=[{"content": prompt}],
                output_messages=output_messages,
                metadata={"stream": stream, "n": n, "stream_options": {"include_usage": include_usage}},
                token_metrics=token_metrics,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
            )
        )

    @pytest.mark.parametrize(
        "stream,n,include_usage",
        [
            (True, 1, True),
            (True, 2, True),
            (False, 1, True),
            (False, 2, True),
            (True, 1, False),
            (True, 2, False),
            (False, 1, False),
            (False, 2, False),
        ],
    )
    async def test_atext_completion(
        self, litellm, request_vcr, mock_llmobs_writer, mock_tracer, stream, n, include_usage
    ):
        with request_vcr.use_cassette(get_cassette_name(stream, n, include_usage)):
            prompt = "Hey, what is up?"
            resp = await litellm.atext_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
                stream_options={"include_usage": include_usage},
            )
            if stream:
                output_messages, token_metrics = await async_consume_stream(resp, n, is_completion=True)
            else:
                output_messages, token_metrics = parse_response(resp, is_completion=True)

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gpt-3.5-turbo",
                model_provider="openai",
                input_messages=[{"content": prompt}],
                output_messages=output_messages,
                metadata={"stream": stream, "n": n, "stream_options": {"include_usage": include_usage}},
                token_metrics=token_metrics,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
            )
        )
