import mock
import pytest

from ddtrace._monkey import patch
from ddtrace.contrib.internal.litellm.patch import get_version
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import safe_json
from tests.contrib.litellm.utils import async_consume_stream_aiter
from tests.contrib.litellm.utils import async_consume_stream_anext
from tests.contrib.litellm.utils import consume_stream_iter
from tests.contrib.litellm.utils import consume_stream_next
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
    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion(self, litellm, request_vcr, llmobs_events, test_spans, stream, n, consume_stream):
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

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_exclude_usage(self, litellm, request_vcr, llmobs_events, test_spans, stream, n, consume_stream):
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

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": False}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_with_tools(self, litellm, request_vcr, llmobs_events, test_spans, stream, n, consume_stream):
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

        span = test_spans.pop_traces()[0][0]
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
            tool_definitions=[
                {
                    "name": tools[0]["function"]["name"],
                    "description": tools[0]["function"]["description"],
                    "schema": tools[0]["function"]["parameters"],
                }
            ],
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_acompletion(self, litellm, request_vcr, llmobs_events, test_spans, stream, n, consume_stream):
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
                output_messages, token_metrics = await consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_text_completion(self, litellm, request_vcr, llmobs_events, test_spans, stream, n, consume_stream):
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

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=[{"content": prompt}],
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_atext_completion(self, litellm, request_vcr, llmobs_events, test_spans, stream, n, consume_stream):
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
                output_messages, token_metrics = await consume_stream(resp, n, is_completion=True)
            else:
                output_messages, token_metrics = parse_response(resp, is_completion=True)

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=[{"content": prompt}],
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("ddtrace_global_config", [dict(_llmobs_instrumented_proxy_urls="http://localhost:4000")])
    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_proxy(
        self, litellm, request_vcr_include_localhost, llmobs_events, test_spans, stream, n, consume_stream
    ):
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

        span = test_spans.pop_traces()[0][0]
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
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_base_url_set(
        self, litellm, request_vcr_include_localhost, llmobs_events, test_spans, stream, n, consume_stream
    ):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(stream, n, proxy=True)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
                stream_options={"include_usage": True},
                base_url="http://localhost:4000",
            )
            if stream:
                output_messages, token_metrics = consume_stream(resp, n)
            else:
                output_messages, token_metrics = parse_response(resp)

        span = test_spans.pop_traces()[0][0]
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
                "base_url": "http://localhost:4000",
                "model": "gpt-3.5-turbo",
            },
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_router_completion(
        self, litellm, request_vcr, llmobs_events, test_spans, router, stream, n, consume_stream
    ):
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

        trace = test_spans.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span"]["kind"] == "llm"
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
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_router_acompletion(
        self, litellm, request_vcr, llmobs_events, test_spans, router, stream, n, consume_stream
    ):
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
                output_messages, _ = await consume_stream(resp, n)
            else:
                output_messages, _ = parse_response(resp)

        trace = test_spans.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span"]["kind"] == "llm"
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
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_router_text_completion(
        self, litellm, request_vcr, llmobs_events, test_spans, router, stream, n, consume_stream
    ):
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

        trace = test_spans.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span"]["kind"] == "llm"
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
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_router_atext_completion(
        self, litellm, request_vcr, llmobs_events, test_spans, router, stream, n, consume_stream
    ):
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
                output_messages, _ = await consume_stream(resp, n, is_completion=True)
            else:
                output_messages, _ = parse_response(resp, is_completion=True)

        trace = test_spans.pop_traces()[0]
        assert len(trace) == 2
        router_span = trace[0]

        assert len(llmobs_events) == 2
        router_event = llmobs_events[1]
        llm_event = llmobs_events[0]

        assert llm_event["meta"]["span"]["kind"] == "llm"
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
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    @pytest.mark.skip(reason="Patching Open AI to be used within the LiteLLM library appears to be flaky")
    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_openai_enabled(
        self, litellm, request_vcr, llmobs_events, test_spans, stream, n, consume_stream
    ):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            patch(openai=True)

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

    def test_completion_anthropic_token_metrics(self, litellm, request_vcr, llmobs_events, test_spans, stream, n):
        """Test that cache token metrics (cache_read, cache_write) are captured for Anthropic models via litellm."""
        if stream or n > 1:
            pytest.skip("Anthropic cassette is non-streamed, single-choice only")

        is_new_litellm = parse_version(get_version()) >= (1, 74, 15)
        cassette_name = "completion_anthropic{}.yaml".format("_v1_74_15" if is_new_litellm else "")

        with request_vcr.use_cassette(cassette_name):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="anthropic/claude-sonnet-4-5-20250929",
                messages=messages,
            )
            output_messages, token_metrics = parse_response(resp)
        # Cassettes have cache_creation_input_tokens=2, all attributed to 5m TTL
        token_metrics["ephemeral_1h_input_tokens"] = 0
        token_metrics["ephemeral_5m_input_tokens"] = 2

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-sonnet-4-5-20250929",
            model_provider="anthropic",
            input_messages=messages,
            output_messages=output_messages,
            metadata={},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

        # Verify cache token metrics are present
        event_metrics = llmobs_events[0]["metrics"]
        assert "cache_read_input_tokens" in event_metrics
        assert "cache_write_input_tokens" in event_metrics

    def test_completion_anthropic_cache_1h_ttl(self, litellm, request_vcr, llmobs_events, test_spans, stream, n):
        """Test that 1h cache TTL breakdown metrics are captured for Anthropic models via litellm."""
        if stream or n > 1:
            pytest.skip("Anthropic cassette is non-streamed, single-choice only")
        if parse_version(get_version()) < (1, 77, 3):
            pytest.skip("cache_creation_token_details not available until litellm 1.77.3")

        large_system_prompt = "Hardware engineering best practices guide: " + "farewell " * 1024

        with request_vcr.use_cassette("completion_anthropic_cache_write_1h_ttl.yaml"):
            messages = [
                {
                    "content": [
                        {
                            "type": "text",
                            "text": large_system_prompt,
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        }
                    ],
                    "role": "system",
                },
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "What are the key principles for designing scalable systems?",
                            "cache_control": {"type": "ephemeral", "ttl": "5m"},
                        }
                    ],
                    "role": "user",
                },
            ]
            resp = litellm.completion(
                model="anthropic/claude-sonnet-4-20250514",
                messages=messages,
            )
            output_messages, token_metrics = parse_response(resp)
        token_metrics["ephemeral_1h_input_tokens"] = 2056
        token_metrics["ephemeral_5m_input_tokens"] = 14

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=mock.ANY,
            output_messages=output_messages,
            metadata={},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

    def test_completion_with_reasoning(self, litellm, request_vcr, llmobs_events, test_spans, stream, n):
        """Test that reasoning_content and reasoning_output_tokens are captured for models with reasoning."""
        if stream or n > 1:
            pytest.skip("Reasoning cassette is non-streamed, single-choice only")

        with request_vcr.use_cassette("completion_reasoning.yaml"):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="o3-mini",
                messages=messages,
            )
            output_messages, token_metrics = parse_response(resp)

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="o3-mini",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={},
            token_metrics=token_metrics,
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"},
        )

        # Verify reasoning output message is present
        event_output = llmobs_events[0]["meta"]["output"]["messages"]
        assert any(msg.get("role") == "reasoning" for msg in event_output)

        # Verify reasoning_output_tokens metric is present
        event_metrics = llmobs_events[0]["metrics"]
        assert "reasoning_output_tokens" in event_metrics
        assert event_metrics["reasoning_output_tokens"] == 15


_OPENAI_ENABLED = "ddtrace.llmobs._integrations.litellm.LLMObs._integration_is_enabled"


def test_completion_litellm_proxy_model_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, llmobs_events, test_spans
):
    """Regression: model='litellm_proxy/<gpt-model>' with OpenAI integration enabled must still produce LLMObs spans."""
    messages = [{"content": "Hey, what is up?", "role": "user"}]
    with mock.patch(_OPENAI_ENABLED, return_value=True):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(False, 1, proxy=True)):
            litellm.completion(
                model="litellm_proxy/gpt-3.5-turbo",
                messages=messages,
                api_base="http://localhost:4000",
                api_key="<not-a-real-key>",
            )
    assert len(llmobs_events) == 1
    event = llmobs_events[0]
    assert event["meta"]["input"]["messages"] == messages
    assert event["meta"]["output"]["messages"]


def test_completion_use_litellm_proxy_kwarg_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, llmobs_events, test_spans
):
    """Regression: use_litellm_proxy=True with an OpenAI model name must still produce LLMObs spans."""
    messages = [{"content": "Hey, what is up?", "role": "user"}]
    with mock.patch(_OPENAI_ENABLED, return_value=True):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(False, 1, proxy=True)):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                api_base="http://localhost:4000",
                api_key="<not-a-real-key>",
                use_litellm_proxy=True,
            )
    assert len(llmobs_events) == 1
    event = llmobs_events[0]
    assert event["meta"]["input"]["messages"] == messages
    assert event["meta"]["output"]["messages"]


def test_completion_use_litellm_proxy_module_flag_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, llmobs_events, test_spans
):
    """Regression: litellm.use_litellm_proxy = True with an OpenAI model name must still produce LLMObs spans."""
    messages = [{"content": "Hey, what is up?", "role": "user"}]
    with mock.patch(_OPENAI_ENABLED, return_value=True):
        with mock.patch.object(litellm, "use_litellm_proxy", True, create=True):
            with request_vcr_include_localhost.use_cassette(get_cassette_name(False, 1, proxy=True)):
                litellm.completion(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    api_base="http://localhost:4000",
                    api_key="<not-a-real-key>",
                )
    assert len(llmobs_events) == 1
    event = llmobs_events[0]
    assert event["meta"]["input"]["messages"] == messages
    assert event["meta"]["output"]["messages"]


def test_completion_use_litellm_proxy_env_var_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, llmobs_events, test_spans, monkeypatch
):
    """Regression: USE_LITELLM_PROXY=true env var with an OpenAI model name must still produce LLMObs spans."""
    monkeypatch.setenv("USE_LITELLM_PROXY", "true")
    messages = [{"content": "Hey, what is up?", "role": "user"}]
    with mock.patch(_OPENAI_ENABLED, return_value=True):
        with request_vcr_include_localhost.use_cassette(get_cassette_name(False, 1, proxy=True)):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                api_base="http://localhost:4000",
                api_key="<not-a-real-key>",
            )
    assert len(llmobs_events) == 1
    event = llmobs_events[0]
    assert event["meta"]["input"]["messages"] == messages
    assert event["meta"]["output"]["messages"]


@pytest.mark.parametrize(
    "model,stream,openai_enabled,expected",
    [
        # litellm_proxy/ prefix always suppresses downstream check regardless of model name or OpenAI enabled
        ("litellm_proxy/azure-gpt-5-nano", False, True, False),
        ("litellm_proxy/gpt-4o", False, True, False),
        ("litellm_proxy/openai/gpt-4", False, True, False),
        # normal OpenAI/Azure models with OpenAI integration enabled (non-streamed) should return True
        ("gpt-4o", False, True, True),
        ("azure/gpt-4", False, True, True),
        ("openai/gpt-4", False, True, True),
        # streaming disables downstream check
        ("gpt-4o", True, True, False),
        # OpenAI integration disabled disables downstream check
        ("gpt-4o", False, False, False),
        # non-OpenAI models are unaffected
        ("anthropic/claude-3", False, True, False),
    ],
)
def test_has_downstream_openai_span(model, stream, openai_enabled, expected):
    from ddtrace import config
    from ddtrace.llmobs._integrations import LiteLLMIntegration

    integration = LiteLLMIntegration(integration_config=config.litellm)
    kwargs = {"stream": stream}
    with mock.patch("ddtrace.llmobs._integrations.litellm.LLMObs._integration_is_enabled", return_value=openai_enabled):
        assert integration._has_downstream_openai_span(kwargs, model) is expected


def test_has_downstream_openai_span_use_litellm_proxy_kwarg():
    """use_litellm_proxy=True in kwargs suppresses downstream check regardless of model name."""
    from ddtrace import config
    from ddtrace.llmobs._integrations import LiteLLMIntegration

    integration = LiteLLMIntegration(integration_config=config.litellm)
    kwargs = {"stream": False, "use_litellm_proxy": True}
    with mock.patch("ddtrace.llmobs._integrations.litellm.LLMObs._integration_is_enabled", return_value=True):
        assert integration._has_downstream_openai_span(kwargs, "gpt-4o") is False


def test_has_downstream_openai_span_use_litellm_proxy_module_flag():
    """litellm.use_litellm_proxy = True suppresses downstream check regardless of model name."""
    import litellm

    from ddtrace import config
    from ddtrace.llmobs._integrations import LiteLLMIntegration

    integration = LiteLLMIntegration(integration_config=config.litellm)
    kwargs = {"stream": False}
    with mock.patch("ddtrace.llmobs._integrations.litellm.LLMObs._integration_is_enabled", return_value=True):
        with mock.patch.object(litellm, "use_litellm_proxy", True, create=True):
            assert integration._has_downstream_openai_span(kwargs, "gpt-4o") is False


def test_has_downstream_openai_span_use_litellm_proxy_env_var(monkeypatch):
    """USE_LITELLM_PROXY=true env var suppresses downstream check regardless of model name."""
    from ddtrace import config
    from ddtrace.llmobs._integrations import LiteLLMIntegration

    monkeypatch.setenv("USE_LITELLM_PROXY", "true")
    integration = LiteLLMIntegration(integration_config=config.litellm)
    kwargs = {"stream": False}
    with mock.patch("ddtrace.llmobs._integrations.litellm.LLMObs._integration_is_enabled", return_value=True):
        assert integration._has_downstream_openai_span(kwargs, "gpt-4o") is False


def test_enable_llmobs_after_litellm_was_imported(run_python_code_in_subprocess):
    """
    Test that LLMObs.enable() logs a warning if litellm is imported before LLMObs.enable() is called.
    """
    _, err, _, _ = run_python_code_in_subprocess(
        """
import litellm
from ddtrace.llmobs import LLMObs
LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
assert LLMObs.enabled
LLMObs.disable()
"""
    )

    assert ("LLMObs.enable() called after litellm was imported but before it was patched") in err.decode()


def test_import_litellm_after_llmobs_was_enabled(run_python_code_in_subprocess):
    """
    Test that LLMObs.enable() does not logs a warning if litellm is imported after LLMObs.enable() is called.
    """
    _, err, _, _ = run_python_code_in_subprocess(
        """
from ddtrace.llmobs import LLMObs
LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
assert LLMObs.enabled
import litellm
LLMObs.disable()
"""
    )

    assert ("LLMObs.enable() called after litellm was imported but before it was patched") not in err.decode()


def test_shadow_tags_completion_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on LiteLLM spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration

    integration = LiteLLMIntegration(MagicMock())

    response = MagicMock()
    response.usage.prompt_tokens = 7
    response.usage.completion_tokens = 3
    response.usage.total_tokens = 10

    with tracer.trace("litellm.request") as span:
        integration._set_apm_shadow_tags(span, ["gpt-3.5-turbo"], {}, response=response, operation="chat")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "gpt-3.5-turbo"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 7
    assert span.get_metric("_dd.llmobs.output_tokens") == 3
    assert span.get_metric("_dd.llmobs.total_tokens") == 10
