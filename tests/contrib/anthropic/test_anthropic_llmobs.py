from pathlib import Path

import mock
from mock import patch
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import safe_json
from tests.contrib.anthropic.test_anthropic import ANTHROPIC_VERSION
from tests.contrib.anthropic.test_anthropic import BETA_SKIP_REASON
from tests.contrib.anthropic.utils import MOCK_MESSAGES_CREATE_REQUEST
from tests.contrib.anthropic.utils import tools
from tests.llmobs._utils import DEEP_TOOL_SCHEMA
from tests.llmobs._utils import aiterate_stream
from tests.llmobs._utils import anext_stream
from tests.llmobs._utils import assert_llmobs_span_data
from tests.llmobs._utils import iterate_stream
from tests.llmobs._utils import next_stream


WEATHER_PROMPT = "What is the weather in San Francisco, CA?"
WEATHER_OUTPUT_MESSAGE_1 = '<thinking>\nThe get_weather tool is directly relevant for answering this \
question about the weather in a specific location. \n\nThe get_weather tool requires a "location" \
parameter. The user has provided the location of "San Francisco, CA" in their question, so we have \
the necessary information to make the API call.\n\nNo other tools are needed to answer this question. \
We can proceed with calling the get_weather tool with the provided location.\n</thinking>'
WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL = [
    {
        "name": "get_weather",
        "arguments": {"location": "San Francisco, CA"},
        "tool_id": "toolu_01DYJo37oETVsCdLTTcCWcdq",
        "type": "tool_use",
    }
]
WEATHER_OUTPUT_MESSAGE_3 = "Based on the result from the get_weather tool, the current weather in San \
Francisco, CA is 73°F."
WEATHER_TOOL_RESULT = [
    {"result": "The weather is 73f", "tool_id": "toolu_01DYJo37oETVsCdLTTcCWcdq", "type": "tool_result"}
]

EXPECTED_TOOL_DEFINITIONS = [
    {
        "name": "get_weather",
        "description": "Get the weather for a specific location",
        "schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
        },
    }
]


class TestLLMObsAnthropic:
    def test_content_block_stop_without_content_does_not_crash(self):
        """Regression test for beta streaming: content_block_stop can arrive without any content blocks."""
        from ddtrace.contrib.internal.anthropic._streaming import _on_content_block_stop_chunk

        message = {"content": []}
        assert _on_content_block_stop_chunk(chunk=None, message=message) == message

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 37), reason=BETA_SKIP_REASON)
    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_beta_server_tool_use_stream(self, anthropic, anthropic_llmobs, test_spans, request_vcr, consume_stream):
        """Regression test for streamed server-side tool usage/results being captured"""
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_beta_tools_stream.yaml"):
            stream = llm.beta.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": WEATHER_PROMPT}],
                tools=[
                    {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
                    {
                        "name": "get_weather",
                        "description": "Get the weather for a specific location",
                        "input_schema": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                        },
                        "defer_loading": True,
                    },
                ],
                betas=[
                    "context-management-2025-06-27",
                    "context-1m-2025-08-07",
                    "advanced-tool-use-2025-11-20",
                    "interleaved-thinking-2025-05-14",
                ],
                stream=True,
            )
            consume_stream(stream)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-6",
            model_provider="anthropic",
            input_messages=[{"content": WEATHER_PROMPT, "role": "user"}],
            output_messages=[
                {
                    "content": "Let me search for a weather tool to help answer your question!",
                    "role": "assistant",
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "tool_search_tool_regex",
                            "arguments": {"pattern": "weather"},
                            "tool_id": "srvtoolu_01TbRQZ9sz7wNun27iQQ8zKf",
                            "type": "server_tool_use",
                        }
                    ],
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_results": [
                        {
                            "result": '{"tool_references": [{"tool_name": "get_weather",'
                            ' "type": "tool_reference"}],'
                            ' "type": "tool_search_tool_search_result"}',
                            "tool_id": "srvtoolu_01TbRQZ9sz7wNun27iQQ8zKf",
                            "type": "tool_result",
                        }
                    ],
                },
                {"content": "Found it! Let me get the weather for San Francisco, CA now.", "role": "assistant"},
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "get_weather",
                            "arguments": {"location": "San Francisco, CA"},
                            "tool_id": "toolu_01SHf2kbTahK9zYYPDyvNp5r",
                            "type": "tool_use",
                        }
                    ],
                },
            ],
            metadata={"max_tokens": 200},
            metrics={
                "input_tokens": 1595,
                "output_tokens": 143,
                "total_tokens": 1738,
                "cache_write_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=[
                {"name": "tool_search_tool_regex", "description": "", "schema": {}},
                {
                    "name": "get_weather",
                    "description": "",
                    "schema": {},
                },
            ],
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 37), reason=BETA_SKIP_REASON)
    def test_beta_server_tool_use_non_stream(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_tools_server_tool_use.yaml"):
            llm.beta.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": WEATHER_PROMPT}],
                tools=[
                    {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
                    {
                        "name": "get_weather",
                        "description": "Get the weather for a specific location",
                        "input_schema": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                        },
                        "defer_loading": True,
                    },
                ],
                betas=[
                    "context-management-2025-06-27",
                    "context-1m-2025-08-07",
                    "advanced-tool-use-2025-11-20",
                    "interleaved-thinking-2025-05-14",
                ],
            )

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-6",
            model_provider="anthropic",
            input_messages=[{"content": WEATHER_PROMPT, "role": "user"}],
            output_messages=[
                {
                    "content": "Let me search for a weather tool to help answer your question!",
                    "role": "assistant",
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "tool_search_tool_regex",
                            "arguments": {"pattern": "weather"},
                            "tool_id": "srvtoolu_01YBjcsxNhiKh8MPSRLFpXSk",
                            "type": "server_tool_use",
                        }
                    ],
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_results": [
                        {
                            "result": '{"tool_references": [{"tool_name": "get_weather",'
                            ' "type": "tool_reference"}],'
                            ' "type": "tool_search_tool_search_result"}',
                            "tool_id": "srvtoolu_01YBjcsxNhiKh8MPSRLFpXSk",
                            "type": "tool_result",
                        }
                    ],
                },
                {
                    "content": "I found a weather tool! Let me fetch the current weather for San Francisco, CA.",
                    "role": "assistant",
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "get_weather",
                            "arguments": {"location": "San Francisco, CA"},
                            "tool_id": "toolu_013vNgEGWuTc17pHztigU6j2",
                            "type": "tool_use",
                        }
                    ],
                },
            ],
            metadata={"max_tokens": 200},
            metrics={
                "input_tokens": 1595,
                "output_tokens": 146,
                "total_tokens": 1741,
                "cache_write_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=[
                {"name": "tool_search_tool_regex", "description": "", "schema": {}},
                {
                    "name": "get_weather",
                    "description": "",
                    "schema": {},
                },
            ],
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 47), reason="Thinking support requires anthropic>=0.47.0")
    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_stream_with_thinking(self, anthropic, anthropic_llmobs, test_spans, request_vcr, consume_stream):
        """Ensure thinking blocks are properly captured from streamed responses."""
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_stream_thinking.yaml"):
            stream = llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                temperature=1,
                thinking={"type": "enabled", "budget_tokens": 1024},
                messages=[{"role": "user", "content": "What is the best selling book of all time?"}],
                stream=True,
            )
            consume_stream(stream)

            spans = [s for trace in test_spans.pop_traces() for s in trace]
            assert len(spans) == 1
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="llm",
                model_name="claude-sonnet-4-20250514",
                model_provider="anthropic",
                input_messages=[
                    {"content": "What is the best selling book of all time?", "role": "user"},
                ],
                output_messages=[
                    {
                        "content": "Let me think about the best selling book.",
                        "role": "reasoning",
                    },
                    {
                        "content": "The best-selling book of all time is Don Quixote.",
                        "role": "assistant",
                    },
                ],
                metadata={"temperature": 1, "max_tokens": 16000.0},
                metrics={"input_tokens": 50, "output_tokens": 200, "total_tokens": 250},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            )

    @patch("anthropic._base_client.SyncAPIClient.post")
    def test_completion_proxy(
        self,
        mock_anthropic_messages_post,
        anthropic,
        anthropic_llmobs,
        test_spans,
        request_vcr,
    ):
        llm = anthropic.Anthropic(base_url="http://localhost:4000")
        mock_anthropic_messages_post.return_value = MOCK_MESSAGES_CREATE_REQUEST
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello, I am looking for information about some books!"},
                    {"type": "text", "text": "What is the best selling book?"},
                ],
            }
        ]
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=15,
            system="Respond only in all caps.",
            temperature=0.8,
            messages=messages,
        )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="workflow",
            input_value=safe_json(
                [
                    {"content": "Respond only in all caps.", "role": "system"},
                    {"content": "Hello, I am looking for information about some books!", "role": "user"},
                    {"content": "What is the best selling book?", "role": "user"},
                ],
                ensure_ascii=False,
            ),
            output_value=safe_json(
                [{"content": 'THE BEST-SELLING BOOK OF ALL TIME IS "DON', "role": "assistant"}], ensure_ascii=False
            ),
            metadata={"temperature": 0.8, "max_tokens": 15.0},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
        )

        # span created from request with non-proxy URL should result in an LLM span
        llm = anthropic.Anthropic(base_url="http://localhost:8000")
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=15,
            system="Respond only in all caps.",
            temperature=0.8,
            messages=messages,
        )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert _get_llmobs_data_metastruct(spans[0])["meta"]["span"]["kind"] == "llm"

    @patch("anthropic._base_client.SyncAPIClient.post")
    def test_completion_unknown_provider(self, mock_anthropic_messages_post, anthropic, anthropic_llmobs, test_spans):
        """Ensure model_provider is set to 'unknown' when base_url doesn't match any known provider."""
        mock_anthropic_messages_post.return_value = MOCK_MESSAGES_CREATE_REQUEST
        llm = anthropic.Anthropic(base_url="http://localhost:8000")
        llm.messages.create(model="claude-3-opus-20240229", max_tokens=15, messages=[{"role": "user", "content": "Hi"}])
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert _get_llmobs_data_metastruct(spans[0])["meta"]["model_provider"] == "unknown"

    def test_completion(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[
                {"content": "Respond only in all caps.", "role": "system"},
                {"content": "Hello, I am looking for information about some books!", "role": "user"},
                {"content": "What is the best selling book?", "role": "user"},
            ],
            output_messages=[{"content": 'THE BEST-SELLING BOOK OF ALL TIME IS "DON', "role": "assistant"}],
            metadata={"temperature": 0.8, "max_tokens": 15.0},
            metrics={"input_tokens": 32, "output_tokens": 15, "total_tokens": 47},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
        )

    def test_completion_with_multiple_system_prompts(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Ensure llmobs records are emitted for completion endpoints with a list of messages as the system prompt.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_multi_system_prompt.yaml"):
            llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                temperature=0.8,
                system=[
                    {
                        "type": "text",
                        "text": "You are an AI assistant tasked with analyzing literary works.",
                    },
                    {"type": "text", "text": "only respond in all caps", "cache_control": {"type": "ephemeral"}},
                ],
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[
                {
                    "content": "You are an AI assistant tasked with analyzing literary works.",
                    "role": "system",
                },
                {"content": "only respond in all caps", "role": "system"},
                {"content": "Hello, I am looking for information about some books!", "role": "user"},
                {"content": "What is the best selling book?", "role": "user"},
            ],
            output_messages=[{"content": "HELLO THERE! ACCORDING TO VARIOUS SOURCES, THE", "role": "assistant"}],
            metadata={"temperature": 0.8, "max_tokens": 15.0},
            metrics={
                "input_tokens": 43,
                "output_tokens": 15,
                "total_tokens": 58,
                "cache_write_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 0,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
        )

    def test_error(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an error.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic(api_key="invalid_api_key")
        with request_vcr.use_cassette("anthropic_completion_invalid_api_key.yaml"):
            with pytest.raises(anthropic.AuthenticationError):
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

                spans = [s for trace in test_spans.pop_traces() for s in trace]
                assert len(spans) == 1
                assert_llmobs_span_data(
                    _get_llmobs_data_metastruct(spans[0]),
                    span_kind="llm",
                    model_name="claude-3-opus-20240229",
                    model_provider="anthropic",
                    input_messages=[
                        {"content": "Respond only in all caps.", "role": "system"},
                        {"content": "Hello, I am looking for information about some books!", "role": "user"},
                        {"content": "What is the best selling book?", "role": "user"},
                    ],
                    output_messages=[{"content": ""}],
                    error={
                        "type": "anthropic.AuthenticationError",
                        "message": spans[0].get_tag("error.message"),
                        "stack": spans[0].get_tag("error.stack"),
                    },
                    metadata={"temperature": 0.8, "max_tokens": 15.0},
                    tags={
                        "ml_app": "<ml-app-name>",
                        "service": "tests.contrib.anthropic",
                        "integration": "anthropic",
                    },
                )

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_stream(self, anthropic, anthropic_llmobs, test_spans, request_vcr, consume_stream):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an stream input.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_stream.yaml"):
            stream = llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                temperature=0.8,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                            }
                        ],
                    },
                ],
                stream=True,
            )
            consume_stream(stream)

            spans = [s for trace in test_spans.pop_traces() for s in trace]
            assert len(spans) == 1
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="llm",
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {
                        "content": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                        "role": "user",
                    },
                ],
                output_messages=[
                    {
                        "content": 'The phrase "I think, therefore I am" (originally in Latin as',
                        "role": "assistant",
                    }
                ],
                metadata={"temperature": 0.8, "max_tokens": 15.0},
                metrics={"input_tokens": 27, "output_tokens": 15, "total_tokens": 42},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            )

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_stream_helper(self, anthropic, anthropic_llmobs, test_spans, request_vcr, consume_stream):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an stream input.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_stream_helper.yaml"):
            with llm.messages.stream(
                model="claude-3-opus-20240229",
                max_tokens=15,
                temperature=0.8,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                            }
                        ],
                    },
                ],
            ) as stream:
                consume_stream(stream.text_stream)

            message = stream.get_final_message()
            assert message is not None

            message = stream.get_final_text()
            assert message is not None

            spans = [s for trace in test_spans.pop_traces() for s in trace]
            assert len(spans) == 1
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="llm",
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {
                        "content": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                        "role": "user",
                    },
                ],
                output_messages=[
                    {
                        "content": 'The famous philosophical statement "I think, therefore I am" (originally in',
                        "role": "assistant",
                    }
                ],
                metadata={"temperature": 0.8, "max_tokens": 15.0},
                metrics={"input_tokens": 27, "output_tokens": 15, "total_tokens": 42},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            )

    def test_image(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an image input.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_create_image.yaml"):
            llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                temperature=0.8,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Hello, what do you see in the following image?",
                            },
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": Path(__file__).parent.joinpath("images/bits.png"),
                                },
                            },
                        ],
                    },
                ],
            )

            spans = [s for trace in test_spans.pop_traces() for s in trace]
            assert len(spans) == 1
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="llm",
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": "Hello, what do you see in the following image?", "role": "user"},
                    {"content": "([IMAGE DETECTED])", "role": "user"},
                ],
                output_messages=[
                    {
                        "content": 'The image shows the logo for a company or product called "Datadog',
                        "role": "assistant",
                    }
                ],
                metadata={"temperature": 0.8, "max_tokens": 15.0},
                metrics={"input_tokens": 246, "output_tokens": 15, "total_tokens": 261},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    def test_tools_sync(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an stream input.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """

        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_tools.yaml"):
            message = llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=200,
                messages=[{"role": "user", "content": WEATHER_PROMPT}],
                tools=tools,
            )
            assert message is not None

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[{"content": WEATHER_PROMPT, "role": "user"}],
            output_messages=[
                {
                    "content": WEATHER_OUTPUT_MESSAGE_1,
                    "role": "assistant",
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL,
                },
            ],
            metadata={"max_tokens": 200.0},
            metrics={"input_tokens": 599, "output_tokens": 152, "total_tokens": 751},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

        tool = next(c for c in message.content if c.type == "tool_use")
        with request_vcr.use_cassette("anthropic_completion_tools_call_with_tool_result.yaml"):
            if message.stop_reason == "tool_use":
                response = llm.messages.create(
                    model="claude-3-opus-20240229",
                    max_tokens=500,
                    messages=[
                        {"role": "user", "content": WEATHER_PROMPT},
                        {"role": "assistant", "content": message.content},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool.id,
                                    "content": [{"type": "text", "text": "The weather is 73f"}],
                                }
                            ],
                        },
                    ],
                    tools=tools,
                )
                assert response is not None

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[
                {"content": WEATHER_PROMPT, "role": "user"},
                {
                    "content": WEATHER_OUTPUT_MESSAGE_1,
                    "role": "assistant",
                },
                {"content": "", "role": "assistant", "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL},
                {
                    "content": "",
                    "role": "user",
                    "tool_results": WEATHER_TOOL_RESULT,
                },
            ],
            output_messages=[
                {
                    "content": WEATHER_OUTPUT_MESSAGE_3,
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 500.0},
            metrics={"input_tokens": 768, "output_tokens": 29, "total_tokens": 797},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

    @pytest.mark.asyncio
    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    async def test_tools_async(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an stream input.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """

        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_tools.yaml"):
            message = await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=200,
                messages=[{"role": "user", "content": WEATHER_PROMPT}],
                tools=tools,
            )
            assert message is not None

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[{"content": WEATHER_PROMPT, "role": "user"}],
            output_messages=[
                {
                    "content": WEATHER_OUTPUT_MESSAGE_1,
                    "role": "assistant",
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL,
                },
            ],
            metadata={"max_tokens": 200.0},
            metrics={"input_tokens": 599, "output_tokens": 152, "total_tokens": 751},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

        tool = next(c for c in message.content if c.type == "tool_use")
        with request_vcr.use_cassette("anthropic_completion_tools_call_with_tool_result.yaml"):
            if message.stop_reason == "tool_use":
                response = await llm.messages.create(
                    model="claude-3-opus-20240229",
                    max_tokens=500,
                    messages=[
                        {"role": "user", "content": WEATHER_PROMPT},
                        {"role": "assistant", "content": message.content},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool.id,
                                    "content": [{"type": "text", "text": "The weather is 73f"}],
                                }
                            ],
                        },
                    ],
                    tools=tools,
                )
                assert response is not None

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[
                {"content": WEATHER_PROMPT, "role": "user"},
                {
                    "content": WEATHER_OUTPUT_MESSAGE_1,
                    "role": "assistant",
                },
                {"content": "", "role": "assistant", "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL},
                {"content": "", "role": "user", "tool_results": WEATHER_TOOL_RESULT},
            ],
            output_messages=[
                {
                    "content": WEATHER_OUTPUT_MESSAGE_3,
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 500.0},
            metrics={"input_tokens": 768, "output_tokens": 29, "total_tokens": 797},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_tools_sync_stream(self, anthropic, anthropic_llmobs, test_spans, request_vcr, consume_stream):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an stream input.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_tools_stream.yaml"):
            stream = llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=200,
                messages=[{"role": "user", "content": WEATHER_PROMPT}],
                tools=tools,
                stream=True,
            )
            consume_stream(stream)

        message = [
            # this message output differs from the other weather outputs since it was produced from a different
            # streamed response.
            {
                "text": "<thinking>\nThe get_weather tool is relevant for answering this question as it provides "
                + "weather information for a specified location.\n\nThe tool has one required parameter:\n- location "
                + '(string): The user has provided the location as "San Francisco, CA".\n\nNo other tools are needed as'
                + " the location is fully specified. We can proceed with calling the get_weather tool.\n</thinking>",
                "type": "text",
            },
            {
                "name": "get_weather",
                "input": {"location": "San Francisco, CA"},
                "id": "toolu_01DYJo37oETVsCdLTTcCWcdq",
                "type": "tool_use",
            },
        ]

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[{"content": WEATHER_PROMPT, "role": "user"}],
            output_messages=[
                {"content": message[0]["text"], "role": "assistant"},
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "get_weather",
                            "arguments": {"location": "San Francisco, CA"},
                            "tool_id": "toolu_01DYJo37oETVsCdLTTcCWcdq",
                            "type": "tool_use",
                        }
                    ],
                },
            ],
            metadata={"max_tokens": 200.0},
            metrics={"input_tokens": 599, "output_tokens": 135, "total_tokens": 734},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

        with request_vcr.use_cassette("anthropic_completion_tools_call_with_tool_result_stream.yaml"):
            response = llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=500,
                messages=[
                    {"role": "user", "content": WEATHER_PROMPT},
                    {"role": "assistant", "content": message},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_01DYJo37oETVsCdLTTcCWcdq",
                                "content": [{"type": "text", "text": "The weather is 73f"}],
                            }
                        ],
                    },
                ],
                tools=tools,
                stream=True,
            )
            for _ in response:
                pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[
                {"content": WEATHER_PROMPT, "role": "user"},
                {"content": message[0]["text"], "role": "assistant"},
                {"content": "", "role": "assistant", "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL},
                {"content": "", "role": "user", "tool_results": WEATHER_TOOL_RESULT},
            ],
            output_messages=[
                {
                    "content": "\n\n" + WEATHER_OUTPUT_MESSAGE_3[:-1] + " (23°C).",
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 500.0},
            metrics={"input_tokens": 762, "output_tokens": 33, "total_tokens": 795},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

    @pytest.mark.asyncio
    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_tools_async_stream_helper(
        self, anthropic, anthropic_llmobs, test_spans, request_vcr, consume_stream
    ):
        """Ensure llmobs records are emitted for completion endpoints when configured and there is an stream input.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_tools_stream_helper.yaml"):
            async with llm.messages.stream(
                model="claude-3-opus-20240229",
                max_tokens=200,
                messages=[{"role": "user", "content": WEATHER_PROMPT}],
                tools=tools,
            ) as stream:
                await consume_stream(stream.text_stream)

            message = await stream.get_final_message()
            assert message is not None

            raw_message = await stream.get_final_text()
            assert raw_message is not None

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[{"content": WEATHER_PROMPT, "role": "user"}],
            output_messages=[
                {"content": message.content[0].text, "role": "assistant"},
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "get_weather",
                            "arguments": {"location": "San Francisco, CA"},
                            "tool_id": "toolu_01DYJo37oETVsCdLTTcCWcdq",
                            "type": "tool_use",
                        }
                    ],
                },
            ],
            metadata={"max_tokens": 200.0},
            metrics={"input_tokens": 599, "output_tokens": 146, "total_tokens": 745},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

        with request_vcr.use_cassette("anthropic_completion_tools_call_with_tool_result_stream_helper.yaml"):
            async with llm.messages.stream(
                model="claude-3-opus-20240229",
                max_tokens=500,
                messages=[
                    {"role": "user", "content": WEATHER_PROMPT},
                    {"role": "assistant", "content": message.content},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_01DYJo37oETVsCdLTTcCWcdq",
                                "content": [{"type": "text", "text": "The weather is 73f"}],
                            }
                        ],
                    },
                ],
                tools=tools,
            ) as stream:
                await consume_stream(stream.text_stream)

            message_2 = await stream.get_final_message()
            assert message_2 is not None

            raw_message = await stream.get_final_text()
            assert raw_message is not None

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[
                {"content": WEATHER_PROMPT, "role": "user"},
                {"content": message.content[0].text, "role": "assistant"},
                {"content": "", "role": "assistant", "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL},
                {"content": "", "role": "user", "tool_results": WEATHER_TOOL_RESULT},
            ],
            output_messages=[
                {
                    "content": "\n\nThe current weather in San Francisco, CA is 73°F.",
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 500.0},
            metrics={"input_tokens": 762, "output_tokens": 18, "total_tokens": 780},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

    def test_completion_prompt_caching(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        llm = anthropic.Anthropic()
        """Test that prompt caching metrics are properly captured for both cache creation and cache read."""
        large_system_prompt = [
            {
                "type": "text",
                "text": "Hardware engineering best practices guide: " + "farewell " * 1024,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        inference_args = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 100,
            "system": large_system_prompt,
            "temperature": 0.1,
            "extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"},
        }
        with request_vcr.use_cassette("anthropic_completion_cache_write.yaml"):
            llm.messages.create(
                **inference_args,
                messages=[{"role": "user", "content": "What are the key principles for designing scalable systems?"}],
            )
        with request_vcr.use_cassette("anthropic_completion_cache_read.yaml"):
            llm.messages.create(**inference_args, messages=[{"role": "user", "content": "What is a system"}])
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 2

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[
                {
                    "content": large_system_prompt[0]["text"],
                    "role": "system",
                },
                {
                    "content": "What are the key principles for designing scalable systems?",
                    "role": "user",
                },
            ],
            output_messages=[{"content": mock.ANY, "role": "assistant"}],
            metadata={
                "temperature": 0.1,
                "max_tokens": 100.0,
            },
            metrics={
                "input_tokens": 2073,
                "output_tokens": 100,
                "total_tokens": 2173,
                "cache_write_input_tokens": 2055,
                "cache_read_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 2055,
            },
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.anthropic",
                "integration": "anthropic",
            },
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[
                {
                    "content": large_system_prompt[0]["text"],
                    "role": "system",
                },
                {
                    "content": "What is a system",
                    "role": "user",
                },
            ],
            output_messages=[{"content": mock.ANY, "role": "assistant"}],
            metadata={
                "temperature": 0.1,
                "max_tokens": 100.0,
            },
            metrics={
                "input_tokens": 2066,
                "output_tokens": 100,
                "total_tokens": 2166,
                "cache_write_input_tokens": 0,
                "cache_read_input_tokens": 2055,
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 0,
            },
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.anthropic",
                "integration": "anthropic",
            },
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 66), reason="1h cache TTL not available until 0.66.0, skipping.")
    def test_completion_prompt_caching_1h_ttl(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Test that prompt caching metrics with 1h TTL are properly captured."""
        llm = anthropic.Anthropic()
        large_system_prompt = [
            {
                "type": "text",
                "text": "Hardware engineering best practices guide: " + "farewell " * 1024,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        with request_vcr.use_cassette("anthropic_completion_cache_write_1h_ttl.yaml"):
            llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=100,
                system=large_system_prompt,
                temperature=0.1,
                messages=[{"role": "user", "content": "What are the key principles for designing scalable systems?"}],
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[
                {
                    "content": large_system_prompt[0]["text"],
                    "role": "system",
                },
                {
                    "content": "What are the key principles for designing scalable systems?",
                    "role": "user",
                },
            ],
            output_messages=[{"content": mock.ANY, "role": "assistant"}],
            metadata={
                "temperature": 0.1,
                "max_tokens": 100.0,
            },
            metrics={
                "input_tokens": 2073,
                "output_tokens": 100,
                "total_tokens": 2173,
                "cache_write_input_tokens": 2056,
                "cache_read_input_tokens": 0,
                "ephemeral_1h_input_tokens": 2056,
                "ephemeral_5m_input_tokens": 0,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
        )

    def test_completion_stream_prompt_caching(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Test that prompt caching metrics are properly captured for streamed completions."""
        large_system_prompt = [
            {
                "type": "text",
                "text": "Software engineering best practices guide: " + "goodbye " * 1024,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        inference_args = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 100,
            "system": large_system_prompt,
            "temperature": 0.1,
            "extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"},
            "stream": True,
        }
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_stream_cache_write.yaml"):
            stream1 = llm.messages.create(
                **inference_args,
                messages=[{"role": "user", "content": "What are the key principles for designing scalable systems?"}],
            )
            for _ in stream1:
                pass
        with request_vcr.use_cassette("anthropic_completion_stream_cache_read.yaml"):
            stream2 = llm.messages.create(**inference_args, messages=[{"role": "user", "content": "What is a system"}])
            for _ in stream2:
                pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 2

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[
                {
                    "content": large_system_prompt[0]["text"],
                    "role": "system",
                },
                {
                    "content": "What are the key principles for designing scalable systems?",
                    "role": "user",
                },
            ],
            output_messages=[{"content": mock.ANY, "role": "assistant"}],
            metadata={
                "temperature": 0.1,
                "max_tokens": 100.0,
            },
            metrics={
                "input_tokens": 1049,
                "output_tokens": 100,
                "total_tokens": 1149,
                "cache_write_input_tokens": 1031,
                "cache_read_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 1031,
            },
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.anthropic",
                "integration": "anthropic",
            },
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[
                {
                    "content": large_system_prompt[0]["text"],
                    "role": "system",
                },
                {
                    "content": "What is a system",
                    "role": "user",
                },
            ],
            output_messages=[{"content": mock.ANY, "role": "assistant"}],
            metadata={
                "temperature": 0.1,
                "max_tokens": 100.0,
            },
            metrics={
                "input_tokens": 1042,
                "output_tokens": 100,
                "total_tokens": 1142,
                "cache_write_input_tokens": 0,
                "cache_read_input_tokens": 1031,
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 0,
            },
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.anthropic",
                "integration": "anthropic",
            },
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 37), reason=BETA_SKIP_REASON)
    def test_beta_completion(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Ensure llmobs records are emitted for beta completion endpoints."""
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion.yaml"):
            response = llm.beta.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                messages=[{"role": "user", "content": "What does Nietzsche mean by 'God is dead'?"}],
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-3-opus-20240229",
            model_provider="anthropic",
            input_messages=[{"content": "What does Nietzsche mean by 'God is dead'?", "role": "user"}],
            output_messages=[{"content": response.content[0].text, "role": "assistant"}],
            metadata={"max_tokens": 15.0},
            metrics={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 47), reason="Thinking support requires anthropic>=0.47.0")
    def test_completion_with_thinking(
        self,
        anthropic,
        anthropic_llmobs,
        test_spans,
        request_vcr,
    ):
        """Ensure extended thinking content blocks are captured as reasoning messages."""
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_thinking.yaml"):
            llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                temperature=1,
                thinking={"type": "enabled", "budget_tokens": 1024},
                messages=[{"role": "user", "content": "What is the best selling book of all time?"}],
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[{"content": "What is the best selling book of all time?", "role": "user"}],
            output_messages=[
                {
                    "content": "Let me think about the best selling book of all time.",
                    "role": "reasoning",
                },
                {"content": 'THE BEST-SELLING BOOK OF ALL TIME IS "DON QUIXOTE"', "role": "assistant"},
            ],
            metadata={"temperature": 1, "max_tokens": 16000.0},
            metrics={"input_tokens": 50, "output_tokens": 200, "total_tokens": 250},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 47), reason="Thinking support requires anthropic>=0.47.0")
    def test_completion_with_thinking_and_tools(
        self,
        anthropic,
        anthropic_llmobs,
        test_spans,
        request_vcr,
    ):
        """Ensure extended thinking + tool_use content blocks are both captured correctly."""
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_thinking_tools.yaml"):
            llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                temperature=1,
                thinking={"type": "enabled", "budget_tokens": 1024},
                messages=[{"role": "user", "content": WEATHER_PROMPT}],
                tools=tools,
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[{"content": WEATHER_PROMPT, "role": "user"}],
            output_messages=[
                {
                    "content": "I need to use the get_weather tool to answer this question.",
                    "role": "reasoning",
                },
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "get_weather",
                            "arguments": {"location": "San Francisco, CA"},
                            "tool_id": "toolu_thinking_001",
                            "type": "tool_use",
                        }
                    ],
                },
            ],
            metadata={"max_tokens": 16000.0, "temperature": 1},
            metrics={"input_tokens": 100, "output_tokens": 150, "total_tokens": 250},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 47), reason="Thinking support requires anthropic>=0.47.0")
    def test_thinking_in_input_messages(
        self,
        anthropic,
        anthropic_llmobs,
        test_spans,
        request_vcr,
    ):
        """Ensure thinking blocks in assistant input messages (tool use continuations) are captured."""
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_thinking_tool_result.yaml"):
            llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                temperature=1,
                thinking={"type": "enabled", "budget_tokens": 1024},
                messages=[
                    {"role": "user", "content": WEATHER_PROMPT},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "I need to check the weather.", "signature": "sig_abc"},
                            {
                                "type": "tool_use",
                                "id": "toolu_thinking_001",
                                "name": "get_weather",
                                "input": {"location": "San Francisco, CA"},
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_thinking_001",
                                "content": [{"type": "text", "text": "The weather is 73f"}],
                            }
                        ],
                    },
                ],
                tools=tools,
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="claude-sonnet-4-20250514",
            model_provider="anthropic",
            input_messages=[
                {"content": WEATHER_PROMPT, "role": "user"},
                {"content": "I need to check the weather.", "role": "reasoning"},
                {
                    "content": "",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "name": "get_weather",
                            "arguments": {"location": "San Francisco, CA"},
                            "tool_id": "toolu_thinking_001",
                            "type": "tool_use",
                        }
                    ],
                },
                {
                    "content": "",
                    "role": "user",
                    "tool_results": [
                        {
                            "result": "The weather is 73f",
                            "tool_id": "toolu_thinking_001",
                            "type": "tool_result",
                        }
                    ],
                },
            ],
            output_messages=[
                {
                    "content": "The weather in San Francisco, CA is currently 73\u00b0F.",
                    "role": "assistant",
                },
            ],
            metadata={"max_tokens": 16000.0, "temperature": 1},
            metrics={"input_tokens": 200, "output_tokens": 30, "total_tokens": 230},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic", "integration": "anthropic"},
            tool_definitions=EXPECTED_TOOL_DEFINITIONS,
        )

    def test_deferred_tool_schema_stripped_in_span(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Regression test: deferred tools (defer_loading=True) should have description and schema
        stripped from LLMObs spans to avoid inflating payload size. Non-deferred tools keep their
        full definitions.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_tools_deferred.yaml"):
            llm.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": "What is the weather in San Francisco, CA?"}],
                tools=[
                    {
                        "name": "get_weather",
                        "description": "Get the weather for a specific location",
                        "input_schema": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                        },
                    },
                    {
                        "name": "search_logs",
                        "description": "Search Datadog logs",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        "defer_loading": True,
                    },
                ],
            )

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        span_event = _get_llmobs_data_metastruct(spans[0])
        assert span_event["meta"]["tool_definitions"] == [
            {
                "name": "get_weather",
                "description": "Get the weather for a specific location",
                "schema": {"type": "object", "properties": {"location": {"type": "string"}}},
            },
            {
                "name": "search_logs",
                "description": "",
                "schema": {},
            },
        ]

    def test_tool_with_deep_schema_has_schema_truncated(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
        """Tool schemas exceeding MAX_TOOL_SCHEMA_DEPTH should be truncated at the depth limit,
        replacing over-limit containers with empty containers while preserving name, description,
        and all fields within the limit. Tools with shallow schemas are unaffected.
        """
        llm = anthropic.Anthropic()
        with request_vcr.use_cassette("anthropic_completion_tools_deep_schema.yaml"):
            llm.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": "What is the weather in San Francisco, CA?"}],
                tools=[
                    {
                        "name": "get_weather",
                        "description": "Get the weather for a specific location",
                        "input_schema": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                        },
                    },
                    {
                        "name": "deep_tool",
                        "description": "A tool with a deeply nested schema",
                        "input_schema": DEEP_TOOL_SCHEMA,
                    },
                ],
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        span_event = _get_llmobs_data_metastruct(spans[0])
        assert span_event["meta"]["tool_definitions"] == [
            {
                "name": "get_weather",
                "description": "Get the weather for a specific location",
                "schema": {"type": "object", "properties": {"location": {"type": "string"}}},
            },
            {
                "name": "deep_tool",
                "description": "A tool with a deeply nested schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "l1": {
                            "type": "object",
                            "properties": {
                                "l2": {
                                    "type": "object",
                                    "properties": {
                                        "l3": {
                                            "type": "object",
                                            "properties": {
                                                "l4": {
                                                    "type": "object",
                                                    "properties": {"l5": {}},
                                                }
                                            },
                                        }
                                    },
                                }
                            },
                        }
                    },
                },
            },
        ]


def test_shadow_tags_chat_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on Anthropic spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.anthropic import AnthropicIntegration

    integration = AnthropicIntegration(MagicMock())
    integration._base_url = "https://api.anthropic.com"

    response = MagicMock()
    response.usage.input_tokens = 12
    response.usage.output_tokens = 8
    response.usage.cache_creation_input_tokens = None
    response.usage.cache_read_input_tokens = None

    with tracer.trace("anthropic.request") as span:
        span._set_attribute("anthropic.request.model", "claude-3-sonnet")
        integration._set_apm_shadow_tags(span, [], {}, response=response)

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "claude-3-sonnet"
    assert span.get_tag("_dd.llmobs.model_provider") == "anthropic"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 12
    assert span.get_metric("_dd.llmobs.output_tokens") == 8
