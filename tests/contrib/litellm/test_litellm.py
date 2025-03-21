import pytest
import os

from tests.contrib.litellm.utils import get_cassette_name

@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
@pytest.mark.snapshot(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"])
def test_litellm_completion(litellm, request_vcr, stream, n):
    with request_vcr.use_cassette(get_cassette_name(stream, n)):
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
    with request_vcr.use_cassette(get_cassette_name(stream, n)):
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
    aws_access_key_id = ""
    aws_secret_access_key = ""
    aws_region_name = ""
    if model == "bedrock/amazon.titan-text-lite-v1":
        aws_access_key_id = "ASIAWYLNJGWWOJPUYN45"
        aws_secret_access_key = "z99X8m2gwAHrXGH8Owd0gzHq5ndHahRW0nEU8xbu"
        aws_region_name = "us-east-1"
    with request_vcr.use_cassette(f"completion_{model}.yaml"):
        messages = [{ "content": "Hey, what is up?","role": "user"}]
        litellm.completion(
            model=model,
            messages=messages,
            stream=False,
            n=1,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_region_name=aws_region_name,
        )