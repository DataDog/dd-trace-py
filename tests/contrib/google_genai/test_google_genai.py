from google.genai import types
import pytest

from tests.contrib.google_genai.utils import EMBED_CONTENT_CONFIG
from tests.contrib.google_genai.utils import FULL_GENERATE_CONTENT_CONFIG
from tests.contrib.google_genai.utils import TOOL_GENERATE_CONTENT_CONFIG
from tests.contrib.google_genai.utils import get_current_weather
from tests.utils import override_global_config


def test_global_tags(mock_generate_content, genai_client, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "Models.generate_content"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("google_genai.request.model") == "gemini-2.0-flash-001"
    assert span.get_tag("google_genai.request.provider") == "google"


def test_google_genai_generate_content(mock_generate_content, genai_client, snapshot_context):
    with snapshot_context(token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content"):
        genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )


def test_google_genai_generate_content_error(mock_generate_content, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_error",
        ignores=["meta.error.stack", "meta.error.message"],
    ):
        with pytest.raises(TypeError):
            genai_client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )


def test_google_genai_generate_content_stream(mock_generate_content_stream, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream"
    ):
        response = genai_client.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        for _ in response:
            pass


def test_google_genai_generate_content_stream_error(mock_generate_content_stream, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream_error",
        ignores=["meta.error.stack", "meta.error.message"],
    ):
        with pytest.raises(TypeError):
            response = genai_client.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
            for _ in response:
                pass


async def test_google_genai_generate_content_async(mock_async_generate_content, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content",
        ignores=["resource"],
    ):
        await genai_client.aio.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )


async def test_google_genai_generate_content_async_error(mock_async_generate_content, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_error",
        ignores=["resource", "meta.error.message", "meta.error.stack"],
    ):
        with pytest.raises(TypeError):
            await genai_client.aio.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )


async def test_google_genai_generate_content_async_stream(
    mock_async_generate_content_stream, genai_client, snapshot_context
):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream",
        ignores=["resource"],
    ):
        response = await genai_client.aio.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        async for _ in response:
            pass


async def test_google_genai_generate_content_async_stream_error(
    mock_async_generate_content_stream, genai_client, snapshot_context
):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream_error",
        ignores=["resource", "meta.error.message", "meta.error.stack"],
    ):
        with pytest.raises(TypeError):
            response = await genai_client.aio.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
            async for _ in response:
                pass


@pytest.mark.parametrize(
    "model_name,expected_provider,expected_model",
    [
        (
            "projects/my-project-id/locations/us-central1/publishers/google/models/gemini-2.0-flash",
            "google",
            "gemini-2.0-flash",
        ),
        ("imagen-1.0", "google", "imagen-1.0"),
        ("models/veo-1.0", "google", "veo-1.0"),
        ("jamba-1.0", "ai21", "jamba-1.0"),
        ("claude-3-opus", "anthropic", "claude-3-opus"),
        ("publishers/meta/models/llama-3.1-405b-instruct-maas", "meta", "llama-3.1-405b-instruct-maas"),
        ("mistral-7b", "mistral", "mistral-7b"),
        ("codestral-22b", "mistral", "codestral-22b"),
        ("deepseek-coder", "deepseek", "deepseek-coder"),
        ("olmo-7b", "ai2", "olmo-7b"),
        ("qodo-7b", "qodo", "qodo-7b"),
        ("mars-7b", "camb.ai", "mars-7b"),
        # edge cases
        ("weird_directory/unknown-model", "custom", "unknown-model"),
        ("", "custom", "custom"),
        ("just-a-slash/", "custom", "custom"),
        ("multiple/slashes/in/path/model-name", "custom", "model-name"),
    ],
)
def test_extract_provider_and_model_name(model_name, expected_provider, expected_model):
    from ddtrace.llmobs._integrations.google_genai_utils import extract_provider_and_model_name

    kwargs = {"model": model_name}
    provider, model = extract_provider_and_model_name(kwargs)

    assert provider == expected_provider
    assert model == expected_model


def test_google_genai_generate_content_with_tools(mock_generate_content_with_tools, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_with_tools"
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

        assert response.function_calls
        function_call_part = response.function_calls[0]
        assert function_call_part.name == "get_current_weather"
        assert function_call_part.args["location"] == "Boston"

        function_result = get_current_weather(**function_call_part.args)

        final_response = genai_client.models.generate_content(
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

        assert final_response.text
        assert "Boston" in final_response.text
        assert "72" in final_response.text


def test_google_genai_embed_content(mock_embed_content, genai_client, snapshot_context):
    with snapshot_context(token="tests.contrib.google_genai.test_google_genai.test_google_genai_embed_content"):
        genai_client.models.embed_content(
            model="text-embedding-004",
            contents=["why is the sky blue?", "What is your age?"],
            config=EMBED_CONTENT_CONFIG,
        )


def test_google_genai_embed_content_error(mock_embed_content, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_embed_content_error",
        ignores=["meta.error.stack", "meta.error.message"],
    ):
        with pytest.raises(TypeError):
            genai_client.models.embed_content(
                model="text-embedding-004",
                contents=["why is the sky blue?", "What is your age?"],
                config=EMBED_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )


async def test_google_genai_embed_content_async(mock_async_embed_content, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_embed_content",
        ignores=["resource"],
    ):
        await genai_client.aio.models.embed_content(
            model="text-embedding-004",
            contents=["why is the sky blue?", "What is your age?"],
            config=EMBED_CONTENT_CONFIG,
        )


async def test_google_genai_embed_content_async_error(mock_async_embed_content, genai_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.google_genai.test_google_genai.test_google_genai_embed_content_error",
        ignores=["resource", "meta.error.message", "meta.error.stack"],
    ):
        with pytest.raises(TypeError):
            await genai_client.aio.models.embed_content(
                model="text-embedding-004",
                contents=["why is the sky blue?", "What is your age?"],
                config=EMBED_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )


@pytest.mark.parametrize(
    "contents,expected",
    [
        ("just a string", [{"role": None, "parts": ["just a string"]}]),
        ({"role": "user", "parts": "hello"}, [{"role": "user", "parts": ["hello"]}]),
        (
            [{"role": "user", "parts": "hello"}, {"role": "assistant", "parts": "hi"}],
            [{"role": "user", "parts": ["hello"]}, {"role": "assistant", "parts": ["hi"]}],
        ),
        (
            type("Content", (), {"role": "system", "parts": ["instruction"]})(),
            [{"role": "system", "parts": ["instruction"]}],
        ),
        (
            [
                type(
                    "Content",
                    (),
                    {"role": "user", "parts": [type("Part", (), {"text": "What are LeBron James stats?"})()]},
                )(),
                "you can only use the vowels e and o",
            ],
            [
                {"role": "user", "parts": [type("Part", (), {"text": "What are LeBron James stats?"})()]},
                {"role": None, "parts": ["you can only use the vowels e and o"]},
            ],
        ),
    ],
)
def test_normalize_contents(contents, expected):
    """Test normalize_contents function with various complex type structures."""
    from ddtrace.llmobs._integrations.google_genai_utils import normalize_contents

    result = normalize_contents(contents)

    # verify structure: list of dicts with role and parts
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, dict)
        assert "role" in item
        assert "parts" in item
        assert isinstance(item["parts"], list)

    # simple cases, do direct comparison
    if all(isinstance(part, str) for item in result for part in item["parts"]):
        assert result == expected
    else:
        # verify structure matches
        assert len(result) == len(expected)
        for actual, expected_item in zip(result, expected):
            assert actual["role"] == expected_item["role"]
            assert len(actual["parts"]) == len(expected_item["parts"])


def test_google_genai_chat_send_message(mock_generate_content, genai_client, snapshot_context):
    with snapshot_context(token="tests.contrib.google_genai.test_google_genai.test_google_genai_chat_send_message"):
        chat = genai_client.chats.create(model="gemini-2.0-flash-001")
        chat.send_message("tell me a story")
