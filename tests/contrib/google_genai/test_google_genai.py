import os

import pytest

from tests.contrib.google_genai.utils import FULL_GENERATE_CONTENT_CONFIG
from tests.utils import override_global_config


def test_global_tags(mock_generate_content, genai, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        client = genai.Client()
        client.models.generate_content(
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


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content")
def test_google_genai_generate_content(mock_generate_content, genai, is_vertex):
    if is_vertex:
        client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    else:
        client = genai.Client()

    client.models.generate_content(
        model="gemini-2.0-flash-001",
        contents="Why is the sky blue? Explain in 2-3 sentences.",
        config=FULL_GENERATE_CONTENT_CONFIG,
    )


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(
    token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_error",
    ignores=["meta.error.stack", "meta.error.message"],
)
def test_google_genai_generate_content_error(mock_generate_content, genai, is_vertex):
    with pytest.raises(TypeError):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()

        client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
            not_an_argument="why am i here?",
        )


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(
    token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream",
)
def test_google_genai_generate_content_stream(mock_generate_content_stream, genai, is_vertex):
    if is_vertex:
        client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    else:
        client = genai.Client()

    response = client.models.generate_content_stream(
        model="gemini-2.0-flash-001",
        contents="Why is the sky blue? Explain in 2-3 sentences.",
        config=FULL_GENERATE_CONTENT_CONFIG,
    )
    for _ in response:
        pass


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(
    token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream_error",
    ignores=["meta.error.stack", "meta.error.message"],
)
def test_google_genai_generate_content_stream_error(mock_generate_content_stream, genai, is_vertex):
    with pytest.raises(TypeError):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()

        response = client.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
            not_an_argument="why am i here?",
        )
        for _ in response:
            pass


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(
    token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content",
    ignores=["resource"],
)
async def test_google_genai_generate_content_async(mock_async_generate_content, genai, is_vertex):
    if is_vertex:
        client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    else:
        client = genai.Client()

    await client.aio.models.generate_content(
        model="gemini-2.0-flash-001",
        contents="Why is the sky blue? Explain in 2-3 sentences.",
        config=FULL_GENERATE_CONTENT_CONFIG,
    )


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(
    token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_error",
    ignores=["resource", "meta.error.message", "meta.error.stack"],
)
async def test_google_genai_generate_content_async_error(mock_async_generate_content, genai, is_vertex):
    with pytest.raises(TypeError):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()

        await client.aio.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
            not_an_argument="why am i here?",
        )


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(
    token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream",
    ignores=["resource"],
)
async def test_google_genai_generate_content_async_stream(mock_async_generate_content_stream, genai, is_vertex):
    if is_vertex:
        client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    else:
        client = genai.Client()

    response = await client.aio.models.generate_content_stream(
        model="gemini-2.0-flash-001",
        contents="Why is the sky blue? Explain in 2-3 sentences.",
        config=FULL_GENERATE_CONTENT_CONFIG,
    )
    async for _ in response:
        pass


@pytest.mark.parametrize("is_vertex", [False, True])
@pytest.mark.snapshot(
    token="tests.contrib.google_genai.test_google_genai.test_google_genai_generate_content_stream_error",
    ignores=["resource", "meta.error.message", "meta.error.stack"],
)
async def test_google_genai_generate_content_async_stream_error(mock_async_generate_content_stream, genai, is_vertex):
    with pytest.raises(TypeError):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()

        response = await client.aio.models.generate_content_stream(
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
    from ddtrace.contrib.internal.google_genai._utils import extract_provider_and_model_name

    kwargs = {"model": model_name}
    provider, model = extract_provider_and_model_name(kwargs)

    assert provider == expected_provider
    assert model == expected_model


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
    from ddtrace.contrib.internal.google_genai._utils import normalize_contents

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
