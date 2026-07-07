import json

import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.contrib.mistralai.utils import CHAT_TOOLS
from tests.contrib.mistralai.utils import FULL_CHAT_REQUEST_KWARGS
from tests.contrib.mistralai.utils import FULL_EMBED_REQUEST_KWARGS
from tests.contrib.mistralai.utils import REASONING_CHAT_REQUEST_KWARGS
from tests.contrib.mistralai.utils import get_expected_chat_metadata
from tests.contrib.mistralai.utils import get_expected_embed_metadata
from tests.contrib.mistralai.utils import get_weather
from tests.llmobs._utils import assert_llmobs_span_data


COMMON_TAGS = {"ml_app": "<ml-app-name>", "service": "tests.contrib.mistralai", "integration": "mistralai"}

STREAM_CHAT_OUTPUT_MESSAGES = [
    {
        "role": "assistant",
        "content": "The sky appears blue due to a phenomenon called **Rayleigh scattering**, which"
        " describes how light interacts with molecules and tiny particles in Earth's atmosphere."
        " Here’s a step-by-step explanation:\n\n### 1. **Sunlight is White Light**\n   -"
        " Sunlight appears white but is actually a mix of all colors (wavelengths) of the visible"
        " spectrum: red, orange, yellow, green, blue, indigo, and violet.\n\n### 2. **Light"
        " Scatters in the Atmosph",
    }
]

REASONING_CHAT_OUTPUT_MESSAGES = [
    {
        "role": "reasoning",
        "content": 'Okay, the user has asked "What is 2+2?" This is a simple arithmetic question. I know that 2+2 '
        'equals 4. So, I should respond with "4".',
    },
    {
        "role": "assistant",
        "content": "What is 2+2?\n${answer}\nTo solve the problem of 2+2, follow these steps:\n\n1. Start with the "
        "first number: 2\n2. Add the second number: 2\n3. Perform the addition: 2 + 2 = 4\n\nThus, the answer is "
        "**4**.",
    },
]

STREAM_TOOL_OUTPUT_MESSAGES = [
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "name": "get_weather",
                "arguments": {"location": "New York City"},
                "tool_id": "ACqYt2JhD",
                "type": "function",
            }
        ],
    }
]

WEATHER_TOOL_DEFINITIONS = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a location.",
        "schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    }
]


def _expected_chat_span_data(
    span, *, model_name="mistral-large-latest", output_messages=None, error=False, **overrides
):
    kwargs = {
        "span_kind": "llm",
        "model_name": model_name,
        "model_provider": "mistral",
        "input_messages": [{"role": "user", "content": "Why is the sky blue?"}],
        "tags": COMMON_TAGS,
    }
    if error:
        kwargs["output_messages"] = output_messages or [{"role": "assistant", "content": ""}]
        kwargs["error"] = {
            "type": "builtins.TypeError",
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        }
    else:
        kwargs["output_messages"] = output_messages or [
            {
                "role": "assistant",
                "content": "The sky appears blue due to a phenomenon called **Rayleigh scattering**, which describes "
                "how light interacts with molecules and tiny particles in Earth's atmosphere. Here’s a "
                "step-by-step explanation:\n\n### 1. **Sunlight is White Light**\n   - Sunlight appears white but is "
                "actually a mix of all colors (wavelengths) of the visible spectrum: red, orange, yellow, green, "
                "blue, indigo, and violet.\n\n### 2. **Scattering by Air Molecules**\n",
            }
        ]
        kwargs["metrics"] = {"input_tokens": 9, "output_tokens": 100, "total_tokens": 109}
        kwargs["metadata"] = get_expected_chat_metadata()
    kwargs.update(overrides)
    return kwargs


def _expected_embed_span_data(span, *, model_name="mistral-embed", error=False, **overrides):
    kwargs = {
        "span_kind": "embedding",
        "model_name": model_name,
        "model_provider": "mistral",
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
        kwargs["output_value"] = "[1 embedding(s) returned with size 1024]"
        kwargs["metrics"] = {"input_tokens": 4, "output_tokens": 0, "total_tokens": 4}
        kwargs["metadata"] = get_expected_embed_metadata()
    kwargs.update(overrides)
    return kwargs


def _expected_tool_first_call_span_data(**overrides):
    kwargs = {
        "span_kind": "llm",
        "model_name": "mistral-large-latest",
        "model_provider": "mistral",
        "input_messages": [{"role": "user", "content": "What's the weather in NYC?"}],
        "output_messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "name": "get_weather",
                        "arguments": {"location": "New York City"},
                        "tool_id": "oG2fuvoqp",
                        "type": "function",
                    }
                ],
            }
        ],
        "metadata": get_expected_chat_metadata(),
        "metrics": {"input_tokens": 72, "output_tokens": 14, "total_tokens": 86},
        "tags": COMMON_TAGS,
        "tool_definitions": WEATHER_TOOL_DEFINITIONS,
    }
    kwargs.update(overrides)
    return kwargs


def _expected_tool_followup_span_data(**overrides):
    kwargs = {
        "span_kind": "llm",
        "model_name": "mistral-large-latest",
        "model_provider": "mistral",
        "input_messages": [
            {"role": "user", "content": "What's the weather in NYC?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "name": "get_weather",
                        "arguments": {"location": "New York City"},
                        "tool_id": "oG2fuvoqp",
                        "type": "function",
                    }
                ],
            },
            {
                "role": "tool",
                "content": json.dumps(
                    {"location": "New York, NY", "temperature": 72, "unit": "fahrenheit", "forecast": "Sunny"}
                ),
            },
        ],
        "output_messages": [
            {
                "role": "assistant",
                "content": "The current weather in **New York City** is **sunny** with a temperature of "
                "**72°F**. Enjoy your day! \U0001f60a",
            }
        ],
        "metadata": get_expected_chat_metadata(),
        "metrics": {"input_tokens": 118, "output_tokens": 32, "total_tokens": 150},
        "tags": COMMON_TAGS,
        "tool_definitions": WEATHER_TOOL_DEFINITIONS,
    }
    kwargs.update(overrides)
    return kwargs


def _expected_reasoning_span_data(span):
    return _expected_chat_span_data(
        span,
        model_name="magistral-medium-latest",
        input_messages=[{"role": "user", "content": "What is 2+2?"}],
        output_messages=REASONING_CHAT_OUTPUT_MESSAGES,
        metrics={"input_tokens": 10, "output_tokens": 112, "total_tokens": 122, "reasoning_output_tokens": 80},
        metadata=get_expected_chat_metadata(REASONING_CHAT_REQUEST_KWARGS),
    )


def test_chat_complete(mistral_client, mistralai_llmobs, test_spans):
    mistral_client.chat.complete(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "Why is the sky blue?"}],
        **FULL_CHAT_REQUEST_KWARGS,
    )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_chat_span_data(spans[0]))


def test_chat_complete_error(mistral_client, mistralai_llmobs, test_spans):
    with pytest.raises(TypeError):
        mistral_client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "Why is the sky blue?"}],
            not_a_real_argument="this should fail",
        )
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_chat_span_data(spans[0], error=True))


async def test_async_chat_complete(mistral_client, mistralai_llmobs, test_spans):
    await mistral_client.chat.complete_async(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "Why is the sky blue?"}],
        **FULL_CHAT_REQUEST_KWARGS,
    )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_chat_span_data(spans[0]))


async def test_async_chat_complete_error(mistral_client, mistralai_llmobs, test_spans):
    with pytest.raises(TypeError):
        await mistral_client.chat.complete_async(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "Why is the sky blue?"}],
            not_a_real_argument="this should fail",
        )
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_chat_span_data(spans[0], error=True))


def test_embed_create(mistral_client, mistralai_llmobs, test_spans):
    mistral_client.embeddings.create(
        model="mistral-embed",
        inputs="Hello world",
        **FULL_EMBED_REQUEST_KWARGS,
    )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_embed_span_data(spans[0], input_documents=[{"text": "Hello world"}]),
    )


def test_embed_create_error(mistral_client, mistralai_llmobs, test_spans):
    with pytest.raises(TypeError):
        mistral_client.embeddings.create(
            model="mistral-embed",
            inputs="Hello world",
            not_a_real_argument="this should fail",
        )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_embed_span_data(spans[0], input_documents=[{"text": "Hello world"}], error=True),
    )


async def test_async_embed_create(mistral_client, mistralai_llmobs, test_spans):
    await mistral_client.embeddings.create_async(
        model="mistral-embed",
        inputs="Hello world",
        **FULL_EMBED_REQUEST_KWARGS,
    )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_embed_span_data(spans[0], input_documents=[{"text": "Hello world"}]),
    )


async def test_async_embed_create_error(mistral_client, mistralai_llmobs, test_spans):
    with pytest.raises(TypeError):
        await mistral_client.embeddings.create_async(
            model="mistral-embed",
            inputs="Hello world",
            not_a_real_argument="this should fail",
        )
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_embed_span_data(spans[0], input_documents=[{"text": "Hello world"}], error=True),
    )


def test_chat_complete_with_tools(mistral_client, mistralai_llmobs, test_spans):
    response = mistral_client.chat.complete(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "What's the weather in NYC?"}],
        tools=CHAT_TOOLS,
        **FULL_CHAT_REQUEST_KWARGS,
    )
    function_calls = response.choices[0].message.tool_calls
    function_result = get_weather(location="New York, NY")

    mistral_client.chat.complete(
        model="mistral-large-latest",
        messages=[
            {"role": "user", "content": "What's the weather in NYC?"},
            response.choices[0].message,
            {
                "role": "tool",
                "content": json.dumps(function_result),
                "tool_call_id": function_calls[0].id,
            },
        ],
        tools=CHAT_TOOLS,
        **FULL_CHAT_REQUEST_KWARGS,
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


def test_chat_complete_with_cached_tokens(mistral_client, mistralai_llmobs, test_spans):
    mistral_client.chat.complete(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "Why is the sky blue?"}],
        **FULL_CHAT_REQUEST_KWARGS,
    )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_chat_span_data(
            spans[0],
            metrics={"input_tokens": 9, "output_tokens": 100, "total_tokens": 109, "cache_read_input_tokens": 5},
        ),
    )


async def test_async_chat_complete_with_tools(mistral_client, mistralai_llmobs, test_spans):
    response = await mistral_client.chat.complete_async(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "What's the weather in NYC?"}],
        tools=CHAT_TOOLS,
        **FULL_CHAT_REQUEST_KWARGS,
    )
    function_calls = response.choices[0].message.tool_calls
    function_result = get_weather(location="New York, NY")

    await mistral_client.chat.complete_async(
        model="mistral-large-latest",
        messages=[
            {"role": "user", "content": "What's the weather in NYC?"},
            response.choices[0].message,
            {
                "role": "tool",
                "content": json.dumps(function_result),
                "tool_call_id": function_calls[0].id,
            },
        ],
        tools=CHAT_TOOLS,
        **FULL_CHAT_REQUEST_KWARGS,
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


def test_chat_stream(mistral_client, mistralai_llmobs, test_spans):
    for _ in mistral_client.chat.stream(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "Why is the sky blue?"}],
        **FULL_CHAT_REQUEST_KWARGS,
    ):
        pass

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_chat_span_data(spans[0], output_messages=STREAM_CHAT_OUTPUT_MESSAGES),
    )


def test_chat_stream_error(mistral_client, mistralai_llmobs, test_spans):
    with pytest.raises(TypeError):
        for _ in mistral_client.chat.stream(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "Why is the sky blue?"}],
            not_a_real_argument="this should fail",
        ):
            pass

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_chat_span_data(spans[0], error=True))


async def test_async_chat_stream(mistral_client, mistralai_llmobs, test_spans):
    async for _ in await mistral_client.chat.stream_async(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "Why is the sky blue?"}],
        **FULL_CHAT_REQUEST_KWARGS,
    ):
        pass

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_chat_span_data(spans[0], output_messages=STREAM_CHAT_OUTPUT_MESSAGES),
    )


async def test_async_chat_stream_error(mistral_client, mistralai_llmobs, test_spans):
    with pytest.raises(TypeError):
        await mistral_client.chat.stream_async(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "Why is the sky blue?"}],
            not_a_real_argument="this should fail",
        )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_chat_span_data(spans[0], error=True))


def test_chat_reasoning(mistral_client, mistralai_llmobs, test_spans):
    mistral_client.chat.complete(
        model="magistral-medium-latest",
        messages=[{"role": "user", "content": "What is 2+2?"}],
        **REASONING_CHAT_REQUEST_KWARGS,
    )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_reasoning_span_data(spans[0]))


async def test_async_chat_reasoning(mistral_client, mistralai_llmobs, test_spans):
    await mistral_client.chat.complete_async(
        model="magistral-medium-latest",
        messages=[{"role": "user", "content": "What is 2+2?"}],
        **REASONING_CHAT_REQUEST_KWARGS,
    )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_reasoning_span_data(spans[0]))


def test_chat_reasoning_stream(mistral_client, mistralai_llmobs, test_spans):
    for _ in mistral_client.chat.stream(
        model="magistral-medium-latest",
        messages=[{"role": "user", "content": "What is 2+2?"}],
        **REASONING_CHAT_REQUEST_KWARGS,
    ):
        pass

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_reasoning_span_data(spans[0]))


async def test_async_chat_reasoning_stream(mistral_client, mistralai_llmobs, test_spans):
    async for _ in await mistral_client.chat.stream_async(
        model="magistral-medium-latest",
        messages=[{"role": "user", "content": "What is 2+2?"}],
        **REASONING_CHAT_REQUEST_KWARGS,
    ):
        pass

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_reasoning_span_data(spans[0]))


def test_chat_stream_with_tools(mistral_client, mistralai_llmobs, test_spans):
    for _ in mistral_client.chat.stream(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "What's the weather in NYC?"}],
        tools=CHAT_TOOLS,
        **FULL_CHAT_REQUEST_KWARGS,
    ):
        pass

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_chat_span_data(
            spans[0],
            input_messages=[{"role": "user", "content": "What's the weather in NYC?"}],
            output_messages=STREAM_TOOL_OUTPUT_MESSAGES,
            metrics={"input_tokens": 72, "output_tokens": 14, "total_tokens": 86},
            tool_definitions=WEATHER_TOOL_DEFINITIONS,
        ),
    )


async def test_async_chat_stream_with_tools(mistral_client, mistralai_llmobs, test_spans):
    async for _ in await mistral_client.chat.stream_async(
        model="mistral-large-latest",
        messages=[{"role": "user", "content": "What's the weather in NYC?"}],
        tools=CHAT_TOOLS,
        **FULL_CHAT_REQUEST_KWARGS,
    ):
        pass

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_chat_span_data(
            spans[0],
            input_messages=[{"role": "user", "content": "What's the weather in NYC?"}],
            output_messages=STREAM_TOOL_OUTPUT_MESSAGES,
            metrics={"input_tokens": 72, "output_tokens": 14, "total_tokens": 86},
            tool_definitions=WEATHER_TOOL_DEFINITIONS,
        ),
    )
