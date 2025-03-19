import pytest

@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
def test_litellm_completion(litellm, request_vcr, stream, n):
    cassette = "completion.yaml" if not stream else "completion_stream.yaml"
    choice_suffix = "_multiple_choices" if n > 1 else ""
    with request_vcr.use_cassette(cassette + choice_suffix):
        messages = [{ "content": "Hey, what is up?","role": "user"}]
        litellm.completion(
            model="gpt-3.5-turbo",
            messages=messages,
            stream=stream,
            n=n,
        )

@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
async def test_litellm_acompletion(litellm, request_vcr, stream, n):
    cassette = "acompletion.yaml" if not stream else "acompletion_stream.yaml"
    choice_suffix = "_multiple_choices" if n > 1 else ""
    with request_vcr.use_cassette(cassette + choice_suffix):
        messages = [{ "content": "Hey, what is up?","role": "user"}]
        await litellm.acompletion(
            model="gpt-3.5-turbo",
            messages=messages,
            stream=stream,
            n=n,
        )

@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)]) 
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
def test_litellm_text_completion(litellm, request_vcr, stream, n):
    cassette = "text_completion.yaml" if not stream else "text_completion_stream.yaml"
    choice_suffix = "_multiple_choices" if n > 1 else ""
    with request_vcr.use_cassette(cassette + choice_suffix):
        litellm.text_completion(
            model="gpt-3.5-turbo",
            prompt="Hello world",
            stream=stream,
            n=n,
        )

@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])  
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
async def test_litellm_atext_completion(litellm, request_vcr, stream, n):
    cassette = "atext_completion.yaml" if not stream else "atext_completion_stream.yaml"
    choice_suffix = "_multiple_choices" if n > 1 else ""
    with request_vcr.use_cassette(cassette + choice_suffix):
        await litellm.atext_completion(
            model="gpt-3.5-turbo",
            prompt="Hello world",
            stream=stream,
            n=n,
        )
