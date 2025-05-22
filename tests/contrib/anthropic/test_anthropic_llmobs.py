from pathlib import Path

from mock import patch
import pytest

from ddtrace.llmobs._utils import safe_json
from tests.contrib.anthropic.test_anthropic import ANTHROPIC_VERSION
from tests.contrib.anthropic.utils import MOCK_MESSAGES_CREATE_REQUEST
from tests.contrib.anthropic.utils import tools
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event

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


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsAnthropic:
    @patch("anthropic._base_client.SyncAPIClient.post")
    def test_completion_proxy(
        self,
        mock_anthropic_messages_post,
        anthropic,
        ddtrace_global_config,
        mock_llmobs_writer,
        mock_tracer,
        request_vcr,
    ):
        """Ensure llmobs records are not emitted for completion endpoints when base_url is specified."""
        llm = anthropic.Anthropic(base_url="http://localhost:4000")
        mock_anthropic_messages_post.return_value = MOCK_MESSAGES_CREATE_REQUEST
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
            _expected_llmobs_non_llm_span_event(
                span,
                "workflow",
                input_value=safe_json([
                    {"content": "Respond only in all caps.", "role": "system"},
                    {"content": "Hello, I am looking for information about some books!", "role": "user"},
                    {"content": "What is the best selling book?", "role": "user"},
                ], ensure_ascii=False),
                output_value=safe_json([{"content": 'THE BEST-SELLING BOOK OF ALL TIME IS "DON', "role": "assistant"}], ensure_ascii=False),
                metadata={"temperature": 0.8, "max_tokens": 15.0},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
        )


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
                token_metrics={"input_tokens": 32, "output_tokens": 15, "total_tokens": 47},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
        )

    def test_completion_with_multiple_system_prompts(
        self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr
    ):
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
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
                token_metrics={"input_tokens": 43, "output_tokens": 15, "total_tokens": 58},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
        )

    def test_error(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
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
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
                    )
                )

    def test_stream(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
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
            for _ in stream:
                pass

            span = mock_tracer.pop_traces()[0][0]
            assert mock_llmobs_writer.enqueue.call_count == 1
            mock_llmobs_writer.enqueue.assert_called_with(
                _expected_llmobs_llm_span_event(
                    span,
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
                    token_metrics={"input_tokens": 27, "output_tokens": 15, "total_tokens": 42},
                    tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
                )
            )

    def test_stream_helper(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
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
                for _ in stream.text_stream:
                    pass

            message = stream.get_final_message()
            assert message is not None

            message = stream.get_final_text()
            assert message is not None

            span = mock_tracer.pop_traces()[0][0]
            assert mock_llmobs_writer.enqueue.call_count == 1
            mock_llmobs_writer.enqueue.assert_called_with(
                _expected_llmobs_llm_span_event(
                    span,
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
                    token_metrics={"input_tokens": 27, "output_tokens": 15, "total_tokens": 42},
                    tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
                )
            )

    def test_image(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
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

            span = mock_tracer.pop_traces()[0][0]
            assert mock_llmobs_writer.enqueue.call_count == 1
            mock_llmobs_writer.enqueue.assert_called_with(
                _expected_llmobs_llm_span_event(
                    span,
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
                    token_metrics={"input_tokens": 246, "output_tokens": 15, "total_tokens": 261},
                    tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
                )
            )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    def test_tools_sync(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
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

        traces = mock_tracer.pop_traces()
        span_1 = traces[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_1,
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
                token_metrics={"input_tokens": 599, "output_tokens": 152, "total_tokens": 751},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
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

        traces = mock_tracer.pop_traces()
        span_2 = traces[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_2,
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": WEATHER_PROMPT, "role": "user"},
                    {
                        "content": WEATHER_OUTPUT_MESSAGE_1,
                        "role": "assistant",
                    },
                    {"content": "", "role": "assistant", "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL},
                    {"content": ["The weather is 73f"], "role": "user"},
                ],
                output_messages=[
                    {
                        "content": WEATHER_OUTPUT_MESSAGE_3,
                        "role": "assistant",
                    }
                ],
                metadata={"max_tokens": 500.0},
                token_metrics={"input_tokens": 768, "output_tokens": 29, "total_tokens": 797},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
        )

    @pytest.mark.asyncio
    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    async def test_tools_async(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
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

        traces = mock_tracer.pop_traces()
        span_1 = traces[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_1,
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
                token_metrics={"input_tokens": 599, "output_tokens": 152, "total_tokens": 751},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
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

        traces = mock_tracer.pop_traces()
        span_2 = traces[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_2,
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": WEATHER_PROMPT, "role": "user"},
                    {
                        "content": WEATHER_OUTPUT_MESSAGE_1,
                        "role": "assistant",
                    },
                    {"content": "", "role": "assistant", "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL},
                    {"content": ["The weather is 73f"], "role": "user"},
                ],
                output_messages=[
                    {
                        "content": WEATHER_OUTPUT_MESSAGE_3,
                        "role": "assistant",
                    }
                ],
                metadata={"max_tokens": 500.0},
                token_metrics={"input_tokens": 768, "output_tokens": 29, "total_tokens": 797},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
        )

    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    def test_tools_sync_stream(self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr):
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
            for _ in stream:
                pass

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
            {"text": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL, "type": "text"},
        ]

        traces = mock_tracer.pop_traces()
        span_1 = traces[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_1,
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
                                "tool_id": "",
                                "type": "tool_use",
                            }
                        ],
                    },
                ],
                metadata={"max_tokens": 200.0},
                token_metrics={"input_tokens": 599, "output_tokens": 135, "total_tokens": 734},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
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

        traces = mock_tracer.pop_traces()
        span_2 = traces[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_2,
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": WEATHER_PROMPT, "role": "user"},
                    {"content": message[0]["text"], "role": "assistant"},
                    {"content": message[1]["text"], "role": "assistant"},
                    {"content": ["The weather is 73f"], "role": "user"},
                ],
                output_messages=[
                    {
                        "content": "\n\n" + WEATHER_OUTPUT_MESSAGE_3[:-1] + " (23°C).",
                        "role": "assistant",
                    }
                ],
                metadata={"max_tokens": 500.0},
                token_metrics={"input_tokens": 762, "output_tokens": 33, "total_tokens": 795},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
        )

    @pytest.mark.asyncio
    @pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
    async def test_tools_async_stream_helper(
        self, anthropic, ddtrace_global_config, mock_llmobs_writer, mock_tracer, request_vcr
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
                async for _ in stream.text_stream:
                    pass

            message = await stream.get_final_message()
            assert message is not None

            raw_message = await stream.get_final_text()
            assert raw_message is not None

        traces = mock_tracer.pop_traces()
        span_1 = traces[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_1,
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
                                "tool_id": "",
                                "type": "tool_use",
                            }
                        ],
                    },
                ],
                metadata={"max_tokens": 200.0},
                token_metrics={"input_tokens": 599, "output_tokens": 146, "total_tokens": 745},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
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
                async for _ in stream.text_stream:
                    pass

            message_2 = await stream.get_final_message()
            assert message_2 is not None

            raw_message = await stream.get_final_text()
            assert raw_message is not None

        traces = mock_tracer.pop_traces()
        span_2 = traces[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span_2,
                model_name="claude-3-opus-20240229",
                model_provider="anthropic",
                input_messages=[
                    {"content": WEATHER_PROMPT, "role": "user"},
                    {"content": message.content[0].text, "role": "assistant"},
                    {"content": "", "role": "assistant", "tool_calls": WEATHER_OUTPUT_MESSAGE_2_TOOL_CALL},
                    {"content": ["The weather is 73f"], "role": "user"},
                ],
                output_messages=[
                    {
                        "content": "\n\nThe current weather in San Francisco, CA is 73°F.",
                        "role": "assistant",
                    }
                ],
                metadata={"max_tokens": 500.0},
                token_metrics={"input_tokens": 762, "output_tokens": 18, "total_tokens": 780},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.anthropic"},
            )
        )
