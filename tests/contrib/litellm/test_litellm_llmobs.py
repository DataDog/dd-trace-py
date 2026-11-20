import inspect
from unittest import mock

import pytest

from ddtrace._monkey import patch
from ddtrace.contrib.internal.litellm.patch import get_version
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import get_llmobs_metrics
from ddtrace.llmobs._utils import get_llmobs_output_messages
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import safe_json
from tests.contrib.litellm.utils import async_consume_stream_aiter
from tests.contrib.litellm.utils import async_consume_stream_anext
from tests.contrib.litellm.utils import consume_stream_iter
from tests.contrib.litellm.utils import consume_stream_next
from tests.contrib.litellm.utils import expected_router_settings
from tests.contrib.litellm.utils import gateway_request_kwargs
from tests.contrib.litellm.utils import get_cassette_name
from tests.contrib.litellm.utils import parse_response
from tests.contrib.litellm.utils import tools
from tests.llmobs._utils import assert_llmobs_span_data
from tests.utils import override_global_config


LITELLM_TAGS = {"ml_app": "<ml-app-name>", "service": "tests.contrib.litellm", "integration": "litellm"}


def _gateway(span):
    metadata_dd = (get_llmobs_metadata(span) or {}).get("_dd")
    return metadata_dd.get("gateway") if isinstance(metadata_dd, dict) else None


def _assert_gateway_is_not_a_tag(span):
    """No tag key names the gateway. A tag value can contain the word, for example in a host name."""
    assert not [key for key in span.get_tags() if "gateway" in key]
    assert not [key for key in (get_llmobs_tags(span) or {}) if "gateway" in key]
    event = span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY)
    assert event is not None
    assert not [tag for tag in event["tags"] if "gateway" in tag.split(":", 1)[0]]


def _assert_gateway_marker(test_spans, use_router, expected):
    """Only the llm span carries the marker. The router span is a workflow span."""
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert sorted(get_llmobs_span_kind(s) for s in spans) == (["llm", "workflow"] if use_router else ["llm"])
    for span in spans:
        assert _gateway(span) == (expected if get_llmobs_span_kind(span) == "llm" else None)
        _assert_gateway_is_not_a_tag(span)


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
    def test_completion(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n, consume_stream):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_exclude_usage(
        self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n, consume_stream
    ):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": False}},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_with_tools(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n, consume_stream):
        if stream and n > 1:
            pytest.skip(
                "Streamed responses with multiple completions and tool calls are not supported: "
                "see open issue https://github.com/BerriAI/litellm/issues/8977"
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
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
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_acompletion(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n, consume_stream):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_text_completion(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n, consume_stream):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=[{"content": prompt}],
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_atext_completion(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n, consume_stream):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-3.5-turbo",
            model_provider="openai",
            input_messages=[{"content": prompt}],
            output_messages=output_messages,
            metadata={"stream": stream, "n": n, "stream_options": {"include_usage": True}},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_proxy(
        self, litellm, request_vcr_include_localhost, litellm_llmobs, test_spans, stream, n, consume_stream
    ):
        with override_global_config({"_llmobs_instrumented_proxy_urls": "http://localhost:4000"}):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
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
            tags=LITELLM_TAGS,
        )
        # A client of the proxy is not inside the proxy.
        assert _gateway(spans[0]) is None

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_base_url_set(
        self, litellm, request_vcr_include_localhost, litellm_llmobs, test_spans, stream, n, consume_stream
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
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
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_router_completion(
        self, litellm, request_vcr, litellm_llmobs, test_spans, router, stream, n, consume_stream
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
        llm_span = trace[1]

        # The downstream LLM span is an "llm" kind named "completion"
        assert get_llmobs_span_kind(llm_span) == "llm"
        assert get_llmobs_span_name(llm_span) == "completion"

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(router_span),
            span_kind="workflow",
            input_value=safe_json(messages, ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_router_acompletion(
        self, litellm, request_vcr, litellm_llmobs, test_spans, router, stream, n, consume_stream
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
        llm_span = trace[1]

        assert get_llmobs_span_kind(llm_span) == "llm"
        assert get_llmobs_span_name(llm_span) == "acompletion"

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(router_span),
            span_kind="workflow",
            input_value=safe_json(messages, ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_router_text_completion(
        self, litellm, request_vcr, litellm_llmobs, test_spans, router, stream, n, consume_stream
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
        llm_span = trace[1]

        assert get_llmobs_span_kind(llm_span) == "llm"
        assert get_llmobs_span_name(llm_span) == "text_completion"

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(router_span),
            span_kind="workflow",
            input_value=safe_json([{"content": prompt}], ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [async_consume_stream_aiter, async_consume_stream_anext])
    async def test_router_atext_completion(
        self, litellm, request_vcr, litellm_llmobs, test_spans, router, stream, n, consume_stream
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
        llm_span = trace[1]

        assert get_llmobs_span_kind(llm_span) == "llm"
        assert get_llmobs_span_name(llm_span) == "atext_completion"

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(router_span),
            span_kind="workflow",
            input_value=safe_json([{"content": prompt}], ensure_ascii=False),
            output_value=safe_json(output_messages, ensure_ascii=False),
            metadata={
                "stream": stream,
                "n": n,
                "stream_options": {"include_usage": True},
                "router_settings": expected_router_settings,
            },
            tags=LITELLM_TAGS,
        )

    @pytest.mark.parametrize("proxied", [True, False])
    @pytest.mark.parametrize("use_router", [False, True])
    @pytest.mark.parametrize("operation", ["completion", "text_completion"])
    def test_gateway_marker(
        self, litellm, request_vcr, litellm_llmobs, test_spans, router, stream, n, operation, use_router, proxied
    ):
        """The LiteLLM proxy server gives proxy_server_request to litellm. Only then is the llm span marked."""
        is_completion = operation == "text_completion"
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            resp = getattr(router if use_router else litellm, operation)(
                **gateway_request_kwargs(operation, proxied, stream, n)
            )
            if stream:
                consume_stream_iter(resp, n, is_completion=is_completion)

        _assert_gateway_marker(test_spans, use_router, expected="litellm" if proxied else None)

    @pytest.mark.parametrize("proxied", [True, False])
    @pytest.mark.parametrize("use_router", [False, True])
    @pytest.mark.parametrize("operation", ["acompletion", "atext_completion"])
    async def test_gateway_marker_async(
        self, litellm, request_vcr, litellm_llmobs, test_spans, router, stream, n, operation, use_router, proxied
    ):
        is_completion = operation == "atext_completion"
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            resp = await getattr(router if use_router else litellm, operation)(
                **gateway_request_kwargs(operation, proxied, stream, n)
            )
            if stream:
                await async_consume_stream_aiter(resp, n, is_completion=is_completion)

        _assert_gateway_marker(test_spans, use_router, expected="litellm" if proxied else None)

    @pytest.mark.parametrize("use_router", [False, True])
    async def test_gateway_marker_does_not_leak_to_the_next_call(
        self, litellm, request_vcr, litellm_llmobs, test_spans, router, stream, n, use_router
    ):
        """In one task, a proxied call comes before a plain call. Only the llm span of the first call is marked."""
        for proxied in (True, False):
            with request_vcr.use_cassette(get_cassette_name(stream, n)):
                resp = await (router if use_router else litellm).acompletion(
                    **gateway_request_kwargs("acompletion", proxied, stream, n)
                )
                if stream:
                    await async_consume_stream_aiter(resp, n)

        spans = sorted((s for trace in test_spans.pop_traces() for s in trace), key=lambda s: s.start_ns)
        assert [_gateway(s) for s in spans if get_llmobs_span_kind(s) == "llm"] == ["litellm", None]

    @pytest.mark.skip(reason="Patching Open AI to be used within the LiteLLM library appears to be flaky")
    @pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
    def test_completion_openai_enabled(
        self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n, consume_stream
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        expected_name = "OpenAI.createChatCompletion" if not stream else "litellm.request"
        assert get_llmobs_span_name(spans[0]) == expected_name

    def test_completion_anthropic_token_metrics(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-5-20250929",
            model_provider="anthropic",
            input_messages=messages,
            output_messages=output_messages,
            metadata={},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

        # Verify cache token metrics are present
        event_metrics = get_llmobs_metrics(spans[0])
        assert "cache_read_input_tokens" in event_metrics
        assert "cache_write_input_tokens" in event_metrics

    def test_completion_anthropic_cache_1h_ttl(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=mock.ANY,
            output_messages=output_messages,
            metadata={},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

    def test_completion_with_reasoning(self, litellm, request_vcr, litellm_llmobs, test_spans, stream, n):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="o3-mini",
            model_provider="openai",
            input_messages=messages,
            output_messages=output_messages,
            metadata={},
            metrics=token_metrics,
            tags=LITELLM_TAGS,
        )

        # Verify reasoning output message is present
        event_output = get_llmobs_output_messages(spans[0])
        assert any(msg.get("role") == "reasoning" for msg in event_output)

        # Verify reasoning_output_tokens metric is present
        event_metrics = get_llmobs_metrics(spans[0])
        assert "reasoning_output_tokens" in event_metrics
        assert event_metrics["reasoning_output_tokens"] == 15


_OPENAI_ENABLED = "ddtrace.contrib.internal.litellm.utils._openai_is_patched"
_SENDS_POST = "ddtrace.contrib.internal.litellm.utils._litellm_sends_openai_chat_with_post"


def test_completion_litellm_proxy_model_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, litellm_llmobs, test_spans
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
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert get_llmobs_input_messages(spans[0]) == messages
    assert get_llmobs_output_messages(spans[0])


def test_completion_use_litellm_proxy_kwarg_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, litellm_llmobs, test_spans
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
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert get_llmobs_input_messages(spans[0]) == messages
    assert get_llmobs_output_messages(spans[0])


def test_completion_use_litellm_proxy_module_flag_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, litellm_llmobs, test_spans
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
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert get_llmobs_input_messages(spans[0]) == messages
    assert get_llmobs_output_messages(spans[0])


def test_completion_use_litellm_proxy_env_var_not_suppressed_when_openai_enabled(
    litellm, request_vcr_include_localhost, litellm_llmobs, test_spans, monkeypatch
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
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert get_llmobs_input_messages(spans[0]) == messages
    assert get_llmobs_output_messages(spans[0])


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
        ("azure/my-deployment", False, True, True),
        ("azure_ai/gpt-4o", False, True, True),
        # streaming disables downstream check
        ("gpt-4o", True, True, False),
        # OpenAI integration disabled disables downstream check
        ("gpt-4o", False, False, False),
        # non-OpenAI models are unaffected
        ("anthropic/claude-3", False, True, False),
        ("openrouter/openai/gpt-4o", False, True, False),
        ("openrouter/openai/gpt-oss-120b", False, True, False),
    ],
)
@pytest.mark.parametrize("operation", ["completion", "text_completion"])
# These kwargs do not change the result for litellm before 1.108.1.
@pytest.mark.parametrize(
    "extra_kwargs",
    [{}, {"custom_llm_provider": "azure"}, {"base_model": "azure/o3-mini"}, {"deployment_id": "my-deploy"}],
)
def test_has_downstream_openai_span(model, stream, openai_enabled, expected, operation, extra_kwargs):
    """litellm before 1.108.1 sends OpenAI chat requests with a create method of the OpenAI SDK."""
    from ddtrace.contrib.internal.litellm.utils import has_downstream_openai_span

    kwargs = {"stream": stream, **extra_kwargs}
    with mock.patch(_OPENAI_ENABLED, return_value=openai_enabled), mock.patch(_SENDS_POST, return_value=False):
        assert has_downstream_openai_span(kwargs, model, operation) is expected


@pytest.mark.parametrize(
    "model,operation,kwargs,openai_enabled,expected",
    [
        # OpenAI chat requests use the post method of the OpenAI client, so no OpenAI span exists.
        ("gpt-4o", "completion", {}, True, False),
        ("openai/gpt-4", "acompletion", {}, True, False),
        ("gpt-3.5-turbo", "text_completion", {}, True, False),
        ("gpt-4o", "atext_completion", {}, True, False),
        ("openai/gpt-3.5-turbo-instruct", "completion", {}, True, False),
        # litellm sends Azure AI requests with its own HTTP client, so no OpenAI span exists.
        ("azure_ai/gpt-4o", "completion", {}, True, False),
        # litellm sends Azure o-series models to the OpenAI chat handler.
        ("azure/o3-mini", "completion", {}, True, False),
        ("azure/o_series/my-deployment", "completion", {}, True, False),
        ("azure/my-deployment", "completion", {"base_model": "azure/o3-mini"}, True, False),
        ("azure/my-deployment", "completion", {"model_info": {"base_model": "azure/o1"}}, True, False),
        ("gpt-4", "completion", {"deployment_id": "o3-mini-deploy"}, True, False),
        # The Azure and text completion handlers call a create method of the OpenAI SDK.
        ("azure/gpt-4", "completion", {}, True, True),
        ("azure/gpt-4", "completion", {"custom_llm_provider": ""}, True, True),
        ("gpt-4o", "completion", {"custom_llm_provider": "azure"}, True, True),
        ("gpt-4o", "completion", {"azure": True}, True, True),
        ("gpt-4", "completion", {"deployment_id": "my-deploy"}, True, True),
        ("azure/o3-mini", "completion", {"deployment_id": "my-deploy"}, True, True),
        ("gpt-35-turbo-instruct", "text_completion", {"custom_llm_provider": "azure_text"}, True, True),
        ("gpt-3.5-turbo-instruct", "completion", {}, True, True),
        ("text-completion-openai/gpt-3.5-turbo-instruct", "completion", {}, True, True),
        ("gpt-3.5-turbo", "text_completion", {"custom_llm_provider": "text-completion-openai"}, True, True),
        # litellm.text_completion() sends an OpenAI model that is not a chat model to the text completion handler.
        ("openai/my-local-model", "text_completion", {}, True, True),
        ("openai/my-local-model", "atext_completion", {}, True, True),
        ("openai/myorg/gpt-4o", "text_completion", {}, True, True),
        ("openai/GPT-4o", "text_completion", {}, True, True),
        # The other checks do not change.
        ("gpt-4o", "completion", {"stream": True}, True, False),
        ("gpt-4o", "completion", {}, False, False),
        ("litellm_proxy/gpt-4o", "completion", {}, True, False),
        ("openrouter/openai/gpt-4o", "completion", {}, True, False),
    ],
)
def test_has_downstream_openai_span_when_litellm_sends_chat_with_post(
    model, operation, kwargs, openai_enabled, expected
):
    """litellm 1.108.1 and later send OpenAI chat requests with the post method of the OpenAI client."""
    from ddtrace.contrib.internal.litellm.utils import has_downstream_openai_span

    with mock.patch(_OPENAI_ENABLED, return_value=openai_enabled), mock.patch(_SENDS_POST, return_value=True):
        assert has_downstream_openai_span(kwargs, model, operation) is expected


def test_litellm_sends_openai_chat_with_post_matches_litellm_source():
    """The version check and a check of the installed litellm source code give the same result."""
    import litellm.llms.openai.openai as litellm_openai

    from ddtrace.contrib.internal.litellm.utils import _litellm_sends_openai_chat_with_post

    calls_create = "chat.completions.with_raw_response.create" in inspect.getsource(litellm_openai)
    assert _litellm_sends_openai_chat_with_post() is not calls_create


@pytest.mark.parametrize("sends_post", [False, True])
def test_has_downstream_openai_span_use_litellm_proxy_kwarg(sends_post):
    """use_litellm_proxy=True in kwargs suppresses downstream check regardless of model name."""
    from ddtrace.contrib.internal.litellm.utils import has_downstream_openai_span

    kwargs = {"stream": False, "use_litellm_proxy": True}
    with mock.patch(_OPENAI_ENABLED, return_value=True), mock.patch(_SENDS_POST, return_value=sends_post):
        assert has_downstream_openai_span(kwargs, "gpt-4o") is False


@pytest.mark.parametrize("sends_post", [False, True])
def test_has_downstream_openai_span_use_litellm_proxy_module_flag(sends_post):
    """litellm.use_litellm_proxy = True suppresses downstream check regardless of model name."""
    import litellm

    from ddtrace.contrib.internal.litellm.utils import has_downstream_openai_span

    kwargs = {"stream": False}
    with mock.patch(_OPENAI_ENABLED, return_value=True), mock.patch(_SENDS_POST, return_value=sends_post):
        with mock.patch.object(litellm, "use_litellm_proxy", True, create=True):
            assert has_downstream_openai_span(kwargs, "gpt-4o") is False


@pytest.mark.parametrize("sends_post", [False, True])
def test_has_downstream_openai_span_use_litellm_proxy_env_var(monkeypatch, sends_post):
    """USE_LITELLM_PROXY=true env var suppresses downstream check regardless of model name."""
    from ddtrace.contrib.internal.litellm.utils import has_downstream_openai_span

    monkeypatch.setenv("USE_LITELLM_PROXY", "true")
    kwargs = {"stream": False}
    with mock.patch(_OPENAI_ENABLED, return_value=True), mock.patch(_SENDS_POST, return_value=sends_post):
        assert has_downstream_openai_span(kwargs, "gpt-4o") is False


def test_gateway_marker_keeps_cost_tags(litellm, request_vcr, litellm_llmobs, test_spans):
    """The marker is merged into meta.metadata._dd, and the cost tags stay there."""
    with litellm_llmobs.annotation_context(tags={"team": "ml-platform"}, cost_tags=["team"]):
        with request_vcr.use_cassette(get_cassette_name(False, 1)):
            litellm.completion(**gateway_request_kwargs("completion", proxied=True, stream=False, n=1))

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert get_llmobs_metadata(spans[0])["_dd"] == {"cost_tags": ["team"], "gateway": "litellm"}


@pytest.mark.subprocess(
    parametrize={
        "_TEST_LITELLM_OPERATION": [
            "completion",
            "acompletion",
            "text_completion",
            "router.completion",
            "router.acompletion",
            "router.atext_completion",
        ]
    },
    env={"OPENAI_API_KEY": "<not-a-real-key>", "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false"},
    err=None,
)
def test_gateway_marker_on_downstream_openai_llm_span():
    """A non-streamed OpenAI call through litellm has one llm span, and this span has the gateway marker.

    Before litellm 1.108.1, the OpenAI span is the llm span. From litellm 1.108.1, the litellm span is the llm span.
    """
    import asyncio
    import inspect
    import os
    from unittest import mock

    from ddtrace import patch

    # This test runs in a new process. The process patches litellm and openai before it imports them,
    # so each run gives the same result.
    patch(litellm=True, openai=True)

    import litellm
    import litellm.llms.openai.openai as litellm_openai

    import ddtrace
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._utils import get_llmobs_metadata
    from ddtrace.llmobs._utils import get_llmobs_span_kind
    from tests.contrib.litellm.utils import gateway_request_kwargs
    from tests.contrib.litellm.utils import get_cassette_name
    from tests.contrib.litellm.utils import get_request_vcr
    from tests.contrib.litellm.utils import model_list
    from tests.utils import DummyWriter

    # The OpenAI integration makes a span only when litellm calls a create method of the OpenAI SDK.
    calls_create = "chat.completions.with_raw_response.create" in inspect.getsource(litellm_openai)
    operation = os.environ["_TEST_LITELLM_OPERATION"]
    method = operation.split(".")[-1]
    use_router = operation.startswith("router.")
    ddtrace.tracer._span_aggregator.writer = DummyWriter(trace_flush_enabled=False)
    LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=False)
    LLMObs._instance._llmobs_span_writer.stop()
    LLMObs._instance._llmobs_span_writer = mock.MagicMock()

    client = litellm.Router(model_list=model_list) if use_router else litellm
    # A chat model also sends a text completion to /chat/completions, so the cassette matches.
    with get_request_vcr().use_cassette(get_cassette_name(False, 1)):
        response = getattr(client, method)(**gateway_request_kwargs(method, proxied=True))
        if asyncio.iscoroutine(response):
            asyncio.run(response)

    spans = ddtrace.tracer._span_aggregator.writer.pop()
    summary = [(s.name, s.resource, s.span_type, get_llmobs_span_kind(s)) for s in spans]
    # Count the LLM Observability spans of the llm kind. The router span is a workflow span.
    llm_spans = [s for s in spans if s.span_type == "llm" and get_llmobs_span_kind(s) == "llm"]
    assert len(llm_spans) == 1, summary
    litellm_span = next(s for s in spans if s.resource == method)
    if calls_create:
        assert len(spans) == (3 if use_router else 2), summary
        openai_span = next(s for s in spans if s.name == "openai.request")
        # The litellm span is not an LLMObs span here, so the OpenAI span is the only llm span.
        assert litellm_span.span_type is None, summary
        assert openai_span.parent_id == litellm_span.span_id, summary
        assert openai_span.span_type == "llm", summary
        assert get_llmobs_span_kind(openai_span) == "llm", summary
        assert get_llmobs_metadata(openai_span)["_dd"] == {"gateway": "litellm"}, summary
    else:
        assert len(spans) == (2 if use_router else 1), summary
        # No OpenAI span exists, so the litellm span is the only llm span.
        assert not [s for s in spans if s.name == "openai.request"], summary
        assert litellm_span.span_type == "llm", summary
        assert get_llmobs_span_kind(litellm_span) == "llm", summary
        assert get_llmobs_metadata(litellm_span)["_dd"] == {"gateway": "litellm"}, summary
    for span in spans:
        assert not [key for key in span.get_tags() if "gateway" in key], summary
    if use_router:
        router_span = next(s for s in spans if s.resource == operation)
        assert get_llmobs_span_kind(router_span) == "workflow", summary
        assert "_dd" not in (get_llmobs_metadata(router_span) or {}), summary


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


def test_azure_shadow_tags_use_response_model(tracer):
    """For azure provider, shadow model_name uses the response model, not the deployment name."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration

    integration = LiteLLMIntegration(MagicMock())
    integration._model_map["azure/my_deployment_name"] = ("my_deployment_name", "azure")

    with tracer.trace("litellm.request") as span:
        span.set_tag("litellm.response.model", "gpt-4o-2024-08-06")
        integration._set_apm_shadow_tags(span, ["azure/my_deployment_name"], {}, response=None, operation="chat")

    assert span.get_tag("_dd.llmobs.model_name") == "gpt-4o-2024-08-06"


def test_shadow_tags_completion_with_cache_tokens(tracer):
    """Verify cache-token shadow metrics propagate from litellm usage to APM span."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration

    integration = LiteLLMIntegration(MagicMock())

    response = MagicMock()
    response.usage.prompt_tokens = 20
    response.usage.completion_tokens = 5
    response.usage.total_tokens = 25
    # litellm normalizes provider cache info under prompt_tokens_details
    response.usage.prompt_tokens_details.cached_tokens = 7
    response.usage.prompt_tokens_details.cache_creation_tokens = 4

    with tracer.trace("litellm.request") as span:
        integration._set_apm_shadow_tags(span, ["gpt-3.5-turbo"], {}, response=response, operation="chat")

    assert span.get_metric("_dd.llmobs.cache_read_input_tokens") == 7
    assert span.get_metric("_dd.llmobs.cache_write_input_tokens") == 4


# --- Azure streaming model name tests ---


def test_stream_handler_captures_chunk_model():
    """Stream handler records the first non-empty chunk.model as response_model."""
    from collections import defaultdict
    from unittest.mock import MagicMock

    from ddtrace.contrib.internal.litellm.utils import BaseLiteLLMStreamHandler

    handler = BaseLiteLLMStreamHandler.__new__(BaseLiteLLMStreamHandler)
    handler.chunks = defaultdict(list)
    handler.spans = []

    chunk = MagicMock()
    chunk.model = "gpt-4o-2024-08-06"
    chunk.choices = []
    chunk.usage = None

    handler._process_chunk(chunk)
    assert handler.response_model == "gpt-4o-2024-08-06"

    # Second chunk does not override the first captured model.
    chunk2 = MagicMock()
    chunk2.model = "different-model"
    chunk2.choices = []
    chunk2.usage = None
    handler._process_chunk(chunk2)
    assert handler.response_model == "gpt-4o-2024-08-06"


def test_stream_handler_skips_empty_chunk_model():
    """Stream handler ignores chunks with no model field."""
    from collections import defaultdict
    from unittest.mock import MagicMock

    from ddtrace.contrib.internal.litellm.utils import BaseLiteLLMStreamHandler

    handler = BaseLiteLLMStreamHandler.__new__(BaseLiteLLMStreamHandler)
    handler.chunks = defaultdict(list)
    handler.spans = []

    empty_chunk = MagicMock()
    empty_chunk.model = None
    empty_chunk.choices = []
    empty_chunk.usage = None
    handler._process_chunk(empty_chunk)
    assert not getattr(handler, "response_model", None)


def test_azure_streaming_model_name_uses_response_model(tracer):
    """For azure provider, _llmobs_set_tags prefers the litellm.response.model span tag."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration

    integration = LiteLLMIntegration(MagicMock())
    integration._model_map["azure/my_deployment_name"] = ("my_deployment_name", "azure")

    with tracer.trace("litellm.request") as span:
        span.set_tag("litellm.response.model", "gpt-4o-2024-08-06")
        integration._llmobs_set_tags(span, ["azure/my_deployment_name"], {}, response=None, operation="chat")

    assert _get_llmobs_data_metastruct(span).get("meta", {}).get("model_name") == "gpt-4o-2024-08-06"


def test_azure_non_streaming_model_name_uses_response_model(tracer):
    """For azure provider, _llmobs_set_tags prefers response.model on non-streaming responses."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration

    integration = LiteLLMIntegration(MagicMock())
    integration._model_map["azure/my_deployment_name"] = ("my_deployment_name", "azure")

    response = MagicMock()
    response.model = "gpt-4o-2024-08-06"
    response.usage.prompt_tokens = 5
    response.usage.completion_tokens = 3
    response.usage.total_tokens = 8

    with tracer.trace("litellm.request") as span:
        integration._llmobs_set_tags(span, ["azure/my_deployment_name"], {}, response=response, operation="chat")

    assert _get_llmobs_data_metastruct(span).get("meta", {}).get("model_name") == "gpt-4o-2024-08-06"


def test_non_azure_streaming_model_name_unchanged(tracer):
    """For non-azure providers, model_name is not overridden and litellm.response.model is not set."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration

    integration = LiteLLMIntegration(MagicMock())
    integration._model_map["openai/gpt-4o"] = ("gpt-4o", "openai")

    with tracer.trace("litellm.request") as span:
        integration._llmobs_set_tags(span, ["openai/gpt-4o"], {}, response=None, operation="chat")

    assert _get_llmobs_data_metastruct(span).get("meta", {}).get("model_name") == "gpt-4o"
    assert span.get_tag("litellm.response.model") is None


def test_azure_ai_model_name_unchanged(tracer):
    """azure_ai provider is not affected — its response.model is 'azure_ai/<deployment>' and must not be preferred."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration

    integration = LiteLLMIntegration(MagicMock())
    integration._model_map["azure_ai/my_deployment"] = ("my_deployment", "azure_ai")

    with tracer.trace("litellm.request") as span:
        span.set_tag("litellm.response.model", "azure_ai/my_deployment")
        integration._llmobs_set_tags(span, ["azure_ai/my_deployment"], {}, response=None, operation="chat")

    assert _get_llmobs_data_metastruct(span).get("meta", {}).get("model_name") == "my_deployment"


@pytest.mark.parametrize("consume_stream", [consume_stream_iter, consume_stream_next])
@pytest.mark.parametrize("positional_model", [False, True])
def test_azure_streaming_completion_e2e(
    litellm, request_vcr, litellm_llmobs, test_spans, consume_stream, positional_model, monkeypatch
):
    """End-to-end: azure streaming via litellm.completion tags the canonical response model,
    not the deployment name. Exercises the stream handler -> litellm.response.model tag -> override.
    Covers the model passed both as a keyword and positionally (the positional form is not present
    in kwargs, so provider detection must read it from args).
    """
    monkeypatch.setenv("AZURE_API_KEY", "<not-a-real-key>")
    monkeypatch.setenv("AZURE_API_BASE", "https://test-azure.openai.azure.com")
    monkeypatch.setenv("AZURE_API_VERSION", "2024-12-01-preview")
    messages = [{"content": "Hey, what is up?", "role": "user"}]
    with request_vcr.use_cassette("completion_azure_stream.yaml"):
        if positional_model:
            resp = litellm.completion(
                "azure/gpt-5.2_2025-12-11", messages=messages, stream=True, stream_options={"include_usage": True}
            )
        else:
            resp = litellm.completion(
                model="azure/gpt-5.2_2025-12-11",
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
            )
        consume_stream(resp, 1)

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert spans[0].get_tag("litellm.response.model") == "gpt-5.2-2025-12-11"
    meta = _get_llmobs_data_metastruct(spans[0]).get("meta", {})
    assert meta.get("model_name") == "gpt-5.2-2025-12-11"
    assert meta.get("model_name") != "gpt-5.2_2025-12-11"
    assert meta.get("model_provider") == "azure"


def test_completion_openrouter_cost(litellm, request_vcr, litellm_llmobs, test_spans):
    """OpenRouter cost (usage.cost) captured through litellm is surfaced on the span's cost metrics.

    OpenRouter returns the billed cost in every response; the litellm integration should emit it as
    ``total_cost`` (plus ``input_cost``/``output_cost`` when they reconcile). Note: the
    ``openrouter/openai/*`` model string is the case where litellm may defer to the openai
    integration via ``has_downstream_openai_span`` — this test also documents whether a span is
    emitted at all for that route.
    """
    with request_vcr.use_cassette("completion_openrouter.yaml"):
        litellm.completion(
            model="openrouter/openai/gpt-oss-120b",
            messages=[{"content": "What is the capital of France?", "role": "user"}],
        )
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    metrics = get_llmobs_metrics(spans[0])
    assert "total_cost" in metrics
    assert metrics["total_cost"] > 0
    if "input_cost" in metrics or "output_cost" in metrics:
        assert round((metrics["input_cost"] + metrics["output_cost"]) * 1e9) == round(metrics["total_cost"] * 1e9)


def test_completion_openrouter_byok_cost(litellm, request_vcr, litellm_llmobs, test_spans):
    """OpenRouter BYOK (bring-your-own-key) upstream cost is surfaced on the span's cost metrics.

    With BYOK, OpenRouter bills ``usage.cost=0`` (the customer pays the provider directly with their
    own key) and reports the real provider cost under ``usage.cost_details.upstream_inference_cost``,
    flagged by ``usage.is_byok=True``. The integration should surface that upstream cost as
    ``total_cost`` rather than the billed 0.
    """
    with request_vcr.use_cassette("completion_openrouter_byok.yaml"):
        resp = litellm.completion(
            model="openrouter/anthropic/claude-opus-5",
            messages=[{"content": "What is the capital of France?", "role": "user"}],
        )

    assert _get_attr(resp.usage, "is_byok", None) is True
    assert _get_attr(resp.usage, "cost", None) == 0
    cost_details = _get_attr(resp.usage, "cost_details", {})
    assert _get_attr(cost_details, "upstream_inference_cost", 0) > 0
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    metrics = get_llmobs_metrics(spans[0])
    # BYOK bills cost=0; the upstream inference cost must still be surfaced as a non-zero total_cost.
    assert metrics["total_cost"] > 0
    if "input_cost" in metrics or "output_cost" in metrics:
        assert round((metrics["input_cost"] + metrics["output_cost"]) * 1e9) == round(metrics["total_cost"] * 1e9)
