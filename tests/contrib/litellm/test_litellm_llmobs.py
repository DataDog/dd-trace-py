import pytest

from ddtrace._trace.pin import Pin
from tests.contrib.litellm.utils import async_consume_stream
from tests.contrib.litellm.utils import consume_stream
from tests.contrib.litellm.utils import get_cassette_name
from tests.contrib.litellm.utils import parse_response
from tests.contrib.litellm.utils import tools
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "stream,n",
    [
        (True, 1),
        (True, 2),
        (False, 1),
        (False, 2),
    ],
)
class TestLLMObsLiteLLM:
    def test_completion(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    def test_completion_exclude_usage(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n, False)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": False},
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": False}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    def test_completion_with_tools(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        if stream and n > 1:
            pytest.skip(
                "Streamed responses with multiple completions and tool calls are not supported: see open issue https://github.com/BerriAI/litellm/issues/8977"
            )
        with request_vcr.use_cassette(get_cassette_name(stream, n, tools=True)):
            messages = [{"content": "What is the weather like in San Francisco, CA?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
                tools=tools,
                tool_choice="auto",
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "tool_choice": "auto",
            },
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    async def test_acompletion(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, token_metrics = await async_consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    def test_text_completion(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            prompt = "Hey, what is up?"
            resp = litellm.text_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n, is_completion=True)
            else:
                output_messages, token_metrics = parse_response(resp, is_completion=True)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=[{"content": prompt}],
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    async def test_atext_completion(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            prompt = "Hey, what is up?"
            resp = await litellm.atext_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, token_metrics = await async_consume_stream(resp, n, is_completion=True)
            else:
                output_messages, token_metrics = parse_response(resp, is_completion=True)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=[{"content": prompt}],
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    @pytest.mark.parametrize("ddtrace_global_config", [dict(_llmobs_integrations_enabled=True)])
    def test_completion_integrations_enabled(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            import openai

            pin = Pin.get_from(openai)
            pin._override(openai, tracer=mock_tracer)

            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        spans = mock_tracer.pop_traces()
        # if streaming, grab the LiteLLM request, otherwise, grab the OpenAI request
        if stream:
            span = spans[0][0]
            metadata = {"stream": stream, "n": n, "stream_options": {"include_usage": True}}
            model_name = "gpt-3.5-turbo"
        else:
            span = spans[0][1]
            # remove parent span since LiteLLM request span will not be submitted to LLMObs
            span._parent = None
            metadata = {
                "n": n,
                "extra_body": {},
                "timeout": 600.0,
                "extra_headers": {"X-Stainless-Raw-Response": "true"},
            }
            model_name = "gpt-3.5-turbo-0125"
        assert len(llmobs_events) == 1
        expected_event = _expected_llmobs_llm_span_event(
            span,
            model_name=model_name,
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata=metadata,
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )
        assert llmobs_events[0] == expected_event

    def test_completion_proxy(self, litellm, request_vcr_include_localhost, llmobs_events, mock_tracer, stream, n):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(stream, n, proxy=True)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
                api_base="http://0.0.0.0:4000",
            )
            if stream:
                consume_stream(resp, n)

        # client side requests made to the proxy are not submitted to LLMObs
        assert len(llmobs_events) == 0
