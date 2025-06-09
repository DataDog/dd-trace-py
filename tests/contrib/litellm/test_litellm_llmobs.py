import pytest

from ddtrace._monkey import patch
from ddtrace._trace.pin import Pin
from ddtrace.llmobs._utils import safe_json
from tests.contrib.litellm.utils import async_consume_stream
from tests.contrib.litellm.utils import consume_stream
from tests.contrib.litellm.utils import expected_router_settings
from tests.contrib.litellm.utils import get_cassette_name
from tests.contrib.litellm.utils import parse_response
from tests.contrib.litellm.utils import tools
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


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

    @pytest.mark.parametrize(
        "ddtrace_global_config", [dict(_llmobs_proxy_urls="http://localhost:4000")]
    )
    def test_completion_proxy(self, litellm, request_vcr_include_localhost, llmobs_events, mock_tracer, stream, n):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(stream, n, proxy=True)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
                api_base="http://localhost:4000",
            )
            if stream:
                output_messages, _ = consume_stream(resp, n)
            else:
                output_messages, _ = parse_response(resp)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
            span,
            span_kind="workflow",
            input_value=safe_json(messages, ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "api_base": "http://localhost:4000",
                "model": "gpt-3.5-turbo",
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )
    
    def test_completion_base_url_set(self, litellm, request_vcr_include_localhost, llmobs_events, mock_tracer, stream, n):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(stream, n, proxy=True)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
                api_base="http://localhost:4000",
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
                "api_base": "http://localhost:4000",
                "model": "gpt-3.5-turbo",
            },
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    def test_router_completion(self, litellm, request_vcr, llmobs_events, mock_tracer, router, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = router.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, _ = consume_stream(resp, n)
            else:
                output_messages, _ = parse_response(resp)

        trace = mock_tracer.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span.kind"] == "llm"
        assert llm_event["name"] == "completion"
        assert router_event == _expected_llmobs_non_llm_span_event(
            router_span,
            span_kind="workflow",
            input_value=safe_json(messages, ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    async def test_router_acompletion(self, litellm, request_vcr, llmobs_events, mock_tracer, router, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = await router.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, _ = await async_consume_stream(resp, n)
            else:
                output_messages, _ = parse_response(resp)

        trace = mock_tracer.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span.kind"] == "llm"
        assert llm_event["name"] == "acompletion"
        assert router_event == _expected_llmobs_non_llm_span_event(
            router_span,
            span_kind="workflow",
            input_value=safe_json(messages, ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    def test_router_text_completion(self, litellm, request_vcr, llmobs_events, mock_tracer, router, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            prompt = "Hey, what is up?"
            resp = router.text_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, _ = consume_stream(resp, n, is_completion=True)
            else:
                output_messages, _ = parse_response(resp, is_completion=True)

        trace = mock_tracer.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span.kind"] == "llm"
        assert llm_event["name"] == "text_completion"
        assert router_event == _expected_llmobs_non_llm_span_event(
            router_span,
            span_kind="workflow",
            input_value=safe_json([{"content": prompt}], ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    async def test_router_atext_completion(self, litellm, request_vcr, llmobs_events, mock_tracer, router, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            prompt = "Hey, what is up?"
            resp = await router.atext_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
            )
            if stream:
                output_messages, _ = await async_consume_stream(resp, n, is_completion=True)
            else:
                output_messages, _ = parse_response(resp, is_completion=True)

        trace = mock_tracer.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span.kind"] == "llm"
        assert llm_event["name"] == "atext_completion"
        assert router_event == _expected_llmobs_non_llm_span_event(
            router_span,
            span_kind="workflow",
            input_value=safe_json([{"content": prompt}], ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm"},
        )

    @pytest.mark.skip(reason="Patching Open AI to be used within the LiteLLM library appears to be flaky")
    def test_completion_openai_enabled(self, litellm, request_vcr, llmobs_events, mock_tracer, stream, n):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            patch(openai=True)
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
                for _ in resp:
                    pass

        assert len(llmobs_events) == 1
        assert llmobs_events[0]["name"] == "OpenAI.createChatCompletion" if not stream else "litellm.request"
