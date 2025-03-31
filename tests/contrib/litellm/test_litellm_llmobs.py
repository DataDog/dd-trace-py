from ddtrace._trace.pin import Pin
from ddtrace.llmobs._llmobs import LLMObs
import pytest

from tests.contrib.litellm.utils import async_consume_stream, get_cassette_name, consume_stream, parse_response, tools
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.utils import DummyTracer


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
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
class TestLLMObsLiteLLM:
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

    def test_completion_with_tools(self, litellm, request_vcr, mock_llmobs_writer, mock_tracer, stream, n, include_usage):
        if stream and n > 1:
            pytest.skip("Streamed responses with multiple completions and tool calls are not supported: see open issue https://github.com/BerriAI/litellm/issues/8977")
        with request_vcr.use_cassette(get_cassette_name(stream, n, include_usage, tools=True)):
            messages = [{"content": "What is the weather like in San Francisco, CA?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": include_usage},
                tools=tools,
                tool_choice="auto",
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
                metadata={"stream": stream, "n": n, "stream_options": {"include_usage": include_usage}, "tool_choice": "auto"},
                token_metrics=token_metrics,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
            )
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

    def test_completion_integrations_enabled(self, litellm, request_vcr, mock_llmobs_writer, mock_tracer, stream, n, include_usage):
        if stream:
            pytest.skip("Streamed Open AI requests will lead to unfinished spans; therefore, skip them for now")
        with request_vcr.use_cassette(get_cassette_name(stream, n, include_usage)):
            LLMObs.disable()

            LLMObs.enable(integrations_enabled=True)
            mock_tracer = DummyTracer()
            import litellm
            import openai

            pin = Pin.get_from(litellm)
            pin._override(litellm, tracer=mock_tracer)
            pin._override(openai, tracer=mock_tracer)

            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": include_usage},
            )
            LLMObs.disable()
            if stream:
                output_messages, token_metrics = consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        openai_span = mock_tracer.pop_traces()[0][1]
        # remove parent span since LiteLLM request span will not be submitted to LLMObs
        openai_span._parent = None
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                openai_span,
                model_name="gpt-3.5-turbo-0125",
                model_provider="openai",
                input_messages=messages,
                output_messages=output_messages,
                metadata={
                    "n": n,
                    "extra_body": {},
                    "timeout": 600.0,
                    "extra_headers": {
                        "X-Stainless-Raw-Response": "true"
                    }
                },
                token_metrics=token_metrics,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
            )
        )
    
    def test_completion_proxy(self, litellm, request_vcr_include_localhost, mock_llmobs_writer, mock_tracer, stream, n, include_usage):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(stream, n, include_usage, proxy=True)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": include_usage},
                api_base="http://0.0.0.0:4000",
            )
            if stream:
                consume_stream(resp, n)

        # client side requests made to the proxy are not submitted to LLMObs
        assert mock_llmobs_writer.enqueue.call_count == 0

    

 