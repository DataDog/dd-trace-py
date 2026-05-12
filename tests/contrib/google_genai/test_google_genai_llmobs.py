import json
from unittest import mock

from google.genai import types
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_output_messages
from tests.contrib.google_genai.utils import EMBED_CONTENT_CONFIG
from tests.contrib.google_genai.utils import FULL_GENERATE_CONTENT_CONFIG
from tests.contrib.google_genai.utils import TOOL_GENERATE_CONTENT_CONFIG
from tests.contrib.google_genai.utils import get_current_weather
from tests.contrib.google_genai.utils import get_expected_metadata
from tests.contrib.google_genai.utils import get_expected_tool_metadata
from tests.llmobs._utils import aiterate_stream
from tests.llmobs._utils import anext_stream
from tests.llmobs._utils import assert_llmobs_span_data
from tests.llmobs._utils import iterate_stream
from tests.llmobs._utils import next_stream


COMMON_TAGS = {"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai", "integration": "google_genai"}

EMBED_METADATA = {
    "auto_truncate": None,
    "mime_type": None,
    "output_dimensionality": 10,
    "task_type": None,
    "title": None,
}

WEATHER_TOOL_DEFINITIONS = [
    {
        "name": "get_current_weather",
        "description": "Mock weather function for tool testing.",
        "schema": {
            "type": "OBJECT",
            "properties": {
                "location": {"type": "STRING"},
                "unit": {"type": "STRING", "default": "fahrenheit"},
            },
            "required": ["location"],
        },
    }
]


def _expected_generate_content_span_data(
    span,
    *,
    model_name="gemini-2.0-flash-001",
    output_messages=None,
    metrics=None,
    error=False,
    **overrides,
):
    kwargs = {
        "span_kind": "llm",
        "model_name": model_name,
        "model_provider": "google",
        "input_messages": [
            {"content": "You are a helpful assistant.", "role": "system"},
            {"content": "Why is the sky blue? Explain in 2-3 sentences.", "role": "user"},
        ],
        "metadata": get_expected_metadata(),
        "tags": COMMON_TAGS,
    }
    if error:
        kwargs["output_messages"] = (
            output_messages if output_messages is not None else [{"content": "", "role": "assistant"}]
        )
        kwargs["error"] = {
            "type": "builtins.TypeError",
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        }
    else:
        kwargs["output_messages"] = (
            output_messages
            if output_messages is not None
            else [{"content": "The sky is blue due to rayleigh scattering", "role": "assistant"}]
        )
        kwargs["metrics"] = (
            metrics if metrics is not None else {"input_tokens": 8, "output_tokens": 9, "total_tokens": 17}
        )
    kwargs.update(overrides)
    return kwargs


def _expected_embed_content_span_data(span, *, error=False, **overrides):
    kwargs = {
        "span_kind": "embedding",
        "model_name": "text-embedding-004",
        "model_provider": "google",
        "input_documents": [
            {"text": "why is the sky blue?"},
            {"text": "What is your age?"},
        ],
        "metadata": EMBED_METADATA,
        "tags": COMMON_TAGS,
    }
    if error:
        kwargs["output_value"] = ""
        kwargs["error"] = {
            "type": "builtins.TypeError",
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        }
    else:
        kwargs["output_value"] = "[2 embedding(s) returned with size 10]"
        kwargs["metrics"] = {"input_tokens": 10, "billable_character_count": 16}
    kwargs.update(overrides)
    return kwargs


def _expected_tool_first_call_span_data(**overrides):
    kwargs = {
        "span_kind": "llm",
        "model_name": "gemini-2.0-flash-001",
        "model_provider": "google",
        "input_messages": [{"content": "What is the weather like in Boston?", "role": "user"}],
        "output_messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "get_current_weather",
                        "arguments": {"location": "Boston"},
                        "tool_id": "",
                        "type": "function_call",
                    }
                ],
            }
        ],
        "metadata": get_expected_tool_metadata(),
        "metrics": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "tags": COMMON_TAGS,
        "tool_definitions": WEATHER_TOOL_DEFINITIONS,
    }
    kwargs.update(overrides)
    return kwargs


def _expected_tool_followup_span_data(**overrides):
    kwargs = {
        "span_kind": "llm",
        "model_name": "gemini-2.0-flash-001",
        "model_provider": "google",
        "input_messages": [
            {"content": "What is the weather like in Boston?", "role": "user"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "get_current_weather",
                        "arguments": {"location": "Boston"},
                        "tool_id": "",
                        "type": "function_call",
                    }
                ],
            },
            {
                "role": "tool",
                "tool_results": [
                    {
                        "name": "get_current_weather",
                        "result": (
                            '{"result": {"location": "Boston", "temperature": 72, '
                            '"unit": "fahrenheit", "forecast": "Sunny with light breeze"}}'
                        ),
                        "tool_id": "",
                        "type": "function_response",
                    }
                ],
            },
        ],
        "output_messages": [
            {
                "content": (
                    "The weather in Boston is sunny with a light breeze and the temperature is 72 degrees Fahrenheit."
                ),
                "role": "assistant",
            }
        ],
        "metadata": get_expected_tool_metadata(),
        "metrics": {"input_tokens": 25, "output_tokens": 20, "total_tokens": 45},
        "tags": COMMON_TAGS,
        "tool_definitions": WEATHER_TOOL_DEFINITIONS,
    }
    kwargs.update(overrides)
    return kwargs


class TestLLMObsGoogleGenAI:
    def test_generate_content(self, genai_client, genai_llmobs, test_spans, mock_generate_content):
        genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0]),
        )

    def test_generate_content_with_reasoning_tokens(
        self, genai_client, genai_llmobs, test_spans, mock_generate_content_with_reasoning
    ):
        genai_client.models.generate_content(
            model="gemini-2.5-pro",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(
                spans[0],
                model_name="gemini-2.5-pro",
                output_messages=[
                    {"content": "Let me think about this...", "role": "assistant"},
                    {"content": "The sky is blue due to rayleigh scattering", "role": "assistant"},
                ],
                metrics={
                    "input_tokens": 8,
                    "output_tokens": 14,
                    "total_tokens": 22,
                    "reasoning_output_tokens": 5,
                },
            ),
        )

    def test_generate_content_error(self, genai_client, genai_llmobs, test_spans, mock_generate_content):
        with pytest.raises(TypeError):
            genai_client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0], error=True),
        )

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_generate_content_stream(
        self, genai_client, genai_llmobs, test_spans, mock_generate_content_stream, consume_stream
    ):
        response = genai_client.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        consume_stream(response)
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0]),
        )

    def test_generate_content_stream_error(self, genai_client, genai_llmobs, test_spans, mock_generate_content_stream):
        with pytest.raises(TypeError):
            genai_client.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0], error=True),
        )

    async def test_generate_content_async(self, genai_client, genai_llmobs, test_spans, mock_async_generate_content):
        await genai_client.aio.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0]),
        )

    async def test_generate_content_async_error(
        self, genai_client, genai_llmobs, test_spans, mock_async_generate_content
    ):
        with pytest.raises(TypeError):
            await genai_client.aio.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0], error=True),
        )

    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_generate_content_stream_async(
        self, genai_client, genai_llmobs, test_spans, mock_async_generate_content_stream, consume_stream
    ):
        response = await genai_client.aio.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        await consume_stream(response)
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0]),
        )

    async def test_generate_content_stream_async_error(
        self, genai_client, genai_llmobs, test_spans, mock_async_generate_content_stream
    ):
        with pytest.raises(TypeError):
            await genai_client.aio.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_generate_content_span_data(spans[0], error=True),
        )

    def test_embed_content(self, genai_client, genai_llmobs, test_spans, mock_embed_content):
        genai_client.models.embed_content(
            model="text-embedding-004",
            contents=["why is the sky blue?", "What is your age?"],
            config=EMBED_CONTENT_CONFIG,
        )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_embed_content_span_data(spans[0]),
        )

    def test_embed_content_error(self, genai_client, genai_llmobs, test_spans, mock_embed_content):
        with pytest.raises(TypeError):
            genai_client.models.embed_content(
                model="text-embedding-004",
                contents=["why is the sky blue?", "What is your age?"],
                config=EMBED_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_embed_content_span_data(spans[0], error=True),
        )

    async def test_embed_content_async(self, genai_client, genai_llmobs, test_spans, mock_async_embed_content):
        await genai_client.aio.models.embed_content(
            model="text-embedding-004",
            contents=["why is the sky blue?", "What is your age?"],
            config=EMBED_CONTENT_CONFIG,
        )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_embed_content_span_data(spans[0]),
        )

    async def test_embed_content_async_error(self, genai_client, genai_llmobs, test_spans, mock_async_embed_content):
        with pytest.raises(TypeError):
            await genai_client.aio.models.embed_content(
                model="text-embedding-004",
                contents=["why is the sky blue?", "What is your age?"],
                config=EMBED_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_embed_content_span_data(spans[0], error=True),
        )

    def test_generate_content_with_tools(
        self, genai_client, genai_llmobs, test_spans, mock_generate_content_with_tools
    ):
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                )
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        function_call_part = response.function_calls[0]
        function_result = get_current_weather(**function_call_part.args)

        genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                ),
                response.candidates[0].content,
                types.Content(
                    role="tool",
                    parts=[
                        types.Part.from_function_response(
                            name=function_call_part.name,
                            response={"result": function_result},
                        )
                    ],
                ),
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 2

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_tool_first_call_span_data(),
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            **_expected_tool_followup_span_data(),
        )

    def test_generate_content_stream_with_tools(
        self, genai_client, genai_llmobs, test_spans, mock_generate_content_stream_with_tools
    ):
        response = genai_client.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                )
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        response_chunks = []
        for chunk in response:
            response_chunks.append(chunk)

        function_call_part = response_chunks[0].function_calls[0]
        function_result = get_current_weather(**function_call_part.args)

        response2 = genai_client.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                ),
                response_chunks[0].candidates[0].content,
                types.Content(
                    role="tool",
                    parts=[
                        types.Part.from_function_response(
                            name=function_call_part.name,
                            response={"result": function_result},
                        )
                    ],
                ),
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        for _ in response2:
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 2

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_tool_first_call_span_data(),
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            **_expected_tool_followup_span_data(),
        )

    async def test_generate_content_async_with_tools(
        self, genai_client, genai_llmobs, test_spans, mock_async_generate_content_with_tools
    ):
        response = await genai_client.aio.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                )
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        function_call_part = response.function_calls[0]
        function_result = get_current_weather(**function_call_part.args)

        await genai_client.aio.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                ),
                response.candidates[0].content,
                types.Content(
                    role="tool",
                    parts=[
                        types.Part.from_function_response(
                            name=function_call_part.name,
                            response={"result": function_result},
                        )
                    ],
                ),
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 2

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_tool_first_call_span_data(),
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            **_expected_tool_followup_span_data(),
        )

    async def test_generate_content_stream_async_with_tools(
        self, genai_client, genai_llmobs, test_spans, mock_async_generate_content_stream_with_tools
    ):
        response = await genai_client.aio.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                )
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        response_chunks = []
        async for chunk in response:
            response_chunks.append(chunk)

        function_call_part = response_chunks[0].function_calls[0]
        function_result = get_current_weather(**function_call_part.args)

        response2 = await genai_client.aio.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What is the weather like in Boston?")],
                ),
                response_chunks[0].candidates[0].content,
                types.Content(
                    role="tool",
                    parts=[
                        types.Part.from_function_response(
                            name=function_call_part.name,
                            response={"result": function_result},
                        )
                    ],
                ),
            ],
            config=TOOL_GENERATE_CONTENT_CONFIG,
        )

        async for _ in response2:
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 2

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_expected_tool_first_call_span_data(),
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            **_expected_tool_followup_span_data(),
        )

    def test_code_execution(self, genai_client_vcr, genai_llmobs, test_spans):
        genai_client_vcr.models.generate_content(
            model="gemini-2.5-flash",
            contents="What is the sum of the first 50 prime numbers? Generate and run code for the calculation, and make sure you get all 50.",  # noqa: E501
            config={"tools": [{"code_execution": {}}]},
        )

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) >= 1
        output_messages = get_llmobs_output_messages(spans[0])
        assert output_messages[0]["content"] == mock.ANY
        assert json.loads(output_messages[1]["content"]) == {
            "language": mock.ANY,
            "code": mock.ANY,
        }
        assert json.loads(output_messages[2]["content"]) == {
            "outcome": mock.ANY,
            "output": mock.ANY,
        }


def test_shadow_tags_generate_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on Google GenAI spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.google_genai import GoogleGenAIIntegration

    integration = GoogleGenAIIntegration(MagicMock())

    response = MagicMock()
    response.usage_metadata.prompt_token_count = 12
    response.usage_metadata.candidates_token_count = 6
    response.usage_metadata.thoughts_token_count = 0
    response.usage_metadata.total_token_count = 18
    response.model_version = "gemini-2.0-flash"

    with tracer.trace("google_genai.request") as span:
        integration._set_apm_shadow_tags(span, [], {"model": "gemini-2.0-flash"}, response=response, operation="llm")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "gemini-2.0-flash"
    assert span.get_tag("_dd.llmobs.model_provider") == "google"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 12
    assert span.get_metric("_dd.llmobs.output_tokens") == 6
    assert span.get_metric("_dd.llmobs.total_tokens") == 18
