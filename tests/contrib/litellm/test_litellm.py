import pytest

from tests.contrib.litellm.utils import get_cassette_name
from tests.utils import override_global_config


def test_global_tags(ddtrace_config_litellm, litellm, request_vcr, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        cassette_name = "completion.yaml"
        with request_vcr.use_cassette(cassette_name):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
            )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "litellm.completion"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("litellm.request.model") == "gpt-3.5-turbo"


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
def test_litellm_completion(litellm, request_vcr, stream, n):
    with request_vcr.use_cassette(get_cassette_name(stream, n)):
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        litellm.completion(
            model="gpt-3.5-turbo",
            messages=messages,
            stream=stream,
            n=n,
        )


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
async def test_litellm_acompletion(litellm, request_vcr, stream, n):
    with request_vcr.use_cassette(get_cassette_name(stream, n)):
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        await litellm.acompletion(
            model="gpt-3.5-turbo",
            messages=messages,
            stream=stream,
            n=n,
        )


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
def test_litellm_text_completion(litellm, request_vcr, stream, n):
    with request_vcr.use_cassette(get_cassette_name(stream, n)):
        litellm.text_completion(
            model="gpt-3.5-turbo",
            prompt="Hello world",
            stream=stream,
            n=n,
        )


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
async def test_litellm_atext_completion(litellm, request_vcr, stream, n):
    with request_vcr.use_cassette(get_cassette_name(stream, n)):
        await litellm.atext_completion(
            model="gpt-3.5-turbo",
            prompt="Hello world",
            stream=stream,
            n=n,
        )


@pytest.mark.parametrize("model", ["vertex_ai/gemini-pro", "anthropic/claude-3-5-sonnet-20240620"])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion_different_models")
def test_litellm_completion_different_models(litellm, request_vcr, model):
    with request_vcr.use_cassette(f"completion_{model.split('/')[0]}.yaml"):
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        litellm.completion(
            model=model,
            messages=messages,
            stream=False,
            n=1,
        )
