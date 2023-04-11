import os

import openai
import pytest
import vcr

from ddtrace.contrib.openai.patch import patch
from ddtrace.contrib.openai.patch import unpatch


# To (re)-generate the cassettes: replace this with a real key, delete the
# old cassettes and re-run the tests.
# NOTE: be sure to check the generated cassettes so they don't contain your
#       API key.
openai.api_key = "not-a-real-key"


@pytest.fixture(autouse=True)
def patch_openai():
    patch()
    yield
    unpatch()


openai_vcr = vcr.VCR(
    cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
    record_mode="once",
    match_on=["path"],
    # Ignore requests to the agent
    ignore_localhost=True,
)


@pytest.mark.snapshot
@pytest.mark.skipif(not hasattr(openai, "Completion"), reason="ChatCompletion not supported for this version of openai")
def test_completion():
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)


@pytest.mark.snapshot
@pytest.mark.skipif(
    not hasattr(openai, "ChatCompletion"), reason="ChatCompletion not supported for this version of openai"
)
def test_chat_completion():
    with openai_vcr.use_cassette("chat_completion.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Who won the world series in 2020?"},
                {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
                {"role": "user", "content": "Where was it played?"},
            ],
            top_p=0.9,
            n=2,
        )


@pytest.mark.snapshot
@pytest.mark.skipif(
    not hasattr(openai, "Embedding") or not hasattr(openai.Embedding, "OBJECT_NAME"),
    reason="embedding not supported for this version of openai",
)
def test_embedding():
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(input="hi this is evan", model="text-embedding-ada-002")
