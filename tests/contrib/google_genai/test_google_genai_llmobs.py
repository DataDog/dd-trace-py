import json
from unittest import mock

from google.genai import types
import pytest

from tests.contrib.google_genai.utils import EMBED_CONTENT_CONFIG
from tests.contrib.google_genai.utils import FULL_GENERATE_CONTENT_CONFIG
from tests.contrib.google_genai.utils import TOOL_GENERATE_CONTENT_CONFIG
from tests.contrib.google_genai.utils import get_current_weather
from tests.contrib.google_genai.utils import get_expected_metadata
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsGoogleGenAI:
    def test_generate_content(self, genai_client, llmobs_events, mock_tracer, mock_generate_content):
        genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_span_event(span)

    def test_generate_content_error(self, genai_client, llmobs_events, mock_tracer, mock_generate_content):
        with pytest.raises(TypeError):
            genai_client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_error_span_event(span)

    def test_generate_content_stream(self, genai_client, llmobs_events, mock_tracer, mock_generate_content_stream):
        response = genai_client.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        for _ in response:
            pass
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_span_event(span)

    def test_generate_content_stream_error(
        self, genai_client, llmobs_events, mock_tracer, mock_generate_content_stream
    ):
        with pytest.raises(TypeError):
            genai_client.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_error_span_event(span)

    async def test_generate_content_async(self, genai_client, llmobs_events, mock_tracer, mock_async_generate_content):
        await genai_client.aio.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_span_event(span)

    async def test_generate_content_async_error(
        self, genai_client, llmobs_events, mock_tracer, mock_async_generate_content
    ):
        with pytest.raises(TypeError):
            await genai_client.aio.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_error_span_event(span)

    async def test_generate_content_stream_async(
        self, genai_client, llmobs_events, mock_tracer, mock_async_generate_content_stream
    ):
        response = await genai_client.aio.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        async for _ in response:
            pass
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_span_event(span)

    async def test_generate_content_stream_async_error(
        self, genai_client, llmobs_events, mock_tracer, mock_async_generate_content_stream
    ):
        with pytest.raises(TypeError):
            await genai_client.aio.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_error_span_event(span)

    def test_embed_content(self, genai_client, llmobs_events, mock_tracer, mock_embed_content):
        genai_client.models.embed_content(
            model="text-embedding-004",
            contents=["why is the sky blue?", "What is your age?"],
            config=EMBED_CONTENT_CONFIG,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_embedding_span_event(span)

    def test_embed_content_error(self, genai_client, llmobs_events, mock_tracer, mock_embed_content):
        with pytest.raises(TypeError):
            genai_client.models.embed_content(
                model="text-embedding-004",
                contents=["why is the sky blue?", "What is your age?"],
                config=EMBED_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_embedding_error_span_event(span)

    async def test_embed_content_async(self, genai_client, llmobs_events, mock_tracer, mock_async_embed_content):
        await genai_client.aio.models.embed_content(
            model="text-embedding-004",
            contents=["why is the sky blue?", "What is your age?"],
            config=EMBED_CONTENT_CONFIG,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_embedding_span_event(span)

    async def test_embed_content_async_error(self, genai_client, llmobs_events, mock_tracer, mock_async_embed_content):
        with pytest.raises(TypeError):
            await genai_client.aio.models.embed_content(
                model="text-embedding-004",
                contents=["why is the sky blue?", "What is your age?"],
                config=EMBED_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_llmobs_embedding_error_span_event(span)

    def test_generate_content_with_tools(
        self, genai_client, llmobs_events, mock_tracer, mock_generate_content_with_tools
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

        traces = mock_tracer.pop_traces()
        assert len(traces) == 2

        first_span = traces[0][0]
        second_span = traces[1][0]

        assert len(llmobs_events) == 2

        expected_first_event = expected_llmobs_tool_call_span_event(first_span)
        assert llmobs_events[0] == expected_first_event

        expected_second_event = expected_llmobs_tool_response_span_event(second_span)
        assert llmobs_events[1] == expected_second_event

    def test_code_execution(self, genai_client_vcr, llmobs_events):
        genai_client_vcr.models.generate_content(
            model="gemini-2.5-flash",
            contents="What is the sum of the first 50 prime numbers? Generate and run code for the calculation, and make sure you get all 50.",  # noqa: E501
            config={"tools": [{"code_execution": {}}]},
        )

        assert llmobs_events[0]["meta"]["output"]["messages"][0]["content"] == mock.ANY
        assert json.loads(llmobs_events[0]["meta"]["output"]["messages"][1]["content"]) == {
            "language": mock.ANY,
            "code": mock.ANY,
        }
        assert json.loads(llmobs_events[0]["meta"]["output"]["messages"][2]["content"]) == {
            "outcome": mock.ANY,
            "output": mock.ANY,
        }


def expected_llmobs_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "You are a helpful assistant.", "role": "system"},
            {"content": "Why is the sky blue? Explain in 2-3 sentences.", "role": "user"},
        ],
        output_messages=[{"content": "The sky is blue due to rayleigh scattering", "role": "assistant"}],
        metadata=get_expected_metadata(),
        token_metrics={"input_tokens": 8, "output_tokens": 9, "total_tokens": 17},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )


def expected_llmobs_error_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "You are a helpful assistant.", "role": "system"},
            {"content": "Why is the sky blue? Explain in 2-3 sentences.", "role": "user"},
        ],
        output_messages=[{"content": "", "role": "assistant"}],
        error="builtins.TypeError",
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
        metadata=get_expected_metadata(),
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )


def expected_llmobs_tool_call_span_event(span):
    from tests.contrib.google_genai.utils import get_expected_tool_metadata

    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "What is the weather like in Boston?", "role": "user"},
        ],
        output_messages=[
            {"role": "assistant", "tool_calls": [{"name": "get_current_weather", "arguments": {"location": "Boston"}}]}
        ],
        metadata=get_expected_tool_metadata(),
        token_metrics={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )


def expected_llmobs_tool_response_span_event(span):
    from tests.contrib.google_genai.utils import get_expected_tool_metadata

    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "What is the weather like in Boston?", "role": "user"},
            {"role": "assistant", "tool_calls": [{"name": "get_current_weather", "arguments": {"location": "Boston"}}]},
            {
                "role": "tool",
                "content": (
                    "{'result': {'location': 'Boston', 'temperature': 72, 'unit': 'fahrenheit', "
                    "'forecast': 'Sunny with light breeze'}}"
                ),
                "tool_id": None,
            },
        ],
        output_messages=[
            {
                "content": (
                    "The weather in Boston is sunny with a light breeze and the temperature is "
                    "72 degrees Fahrenheit."
                ),
                "role": "assistant",
            }
        ],
        metadata=get_expected_tool_metadata(),
        token_metrics={"input_tokens": 25, "output_tokens": 20, "total_tokens": 45},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )


def expected_llmobs_embedding_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        span_kind="embedding",
        model_name="text-embedding-004",
        model_provider="google",
        input_documents=[
            {"text": "why is the sky blue?"},
            {"text": "What is your age?"},
        ],
        output_value="[2 embedding(s) returned with size 10]",
        metadata={
            "auto_truncate": None,
            "mime_type": None,
            "output_dimensionality": 10,
            "task_type": None,
            "title": None,
        },
        token_metrics={"input_tokens": 10, "billable_character_count": 16},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )


def expected_llmobs_embedding_error_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        span_kind="embedding",
        model_name="text-embedding-004",
        model_provider="google",
        input_documents=[
            {"text": "why is the sky blue?"},
            {"text": "What is your age?"},
        ],
        output_value="",
        error="builtins.TypeError",
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
        metadata={
            "auto_truncate": None,
            "mime_type": None,
            "output_dimensionality": 10,
            "task_type": None,
            "title": None,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )
