import os

import openai
import pytest
import vcr

from ddtrace import patch
from ddtrace.contrib.openai.patch import unpatch


# To (re)-generate the cassettes: replace this with a real key, delete the
# old cassettes and re-run the tests.
# NOTE: be sure to check the generated cassettes so they don't contain your
#       API key.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
openai.api_key = "not-a-real-key"
openai_vcr = vcr.VCR(
    cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
    record_mode="once",
    match_on=["path"],
    filter_headers=["authorization"],
    # Ignore requests to the agent
    ignore_localhost=True,
)


@pytest.fixture(autouse=True)
def patch_openai():
    # FIXME: aiohttp spans are not being generated in these tests, it looks like they should be
    #        as a new aiohttp session is created for each request (which should get instrumented).
    #        The __init__ wrapped is called but the _request one is not...
    patch(aiohttp=True, openai=True, requests=True)
    yield
    unpatch()


# @pytest.mark.snapshot
@pytest.mark.skipif(not hasattr(openai, "Completion"), reason="ChatCompletion not supported for this version of openai")
def test_completion():
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)

    from ddtrace.contrib.openai._log import _logs_writer

    _logs_writer.periodic()
    assert 0


@pytest.mark.asyncio
# @pytest.mark.snapshot
async def test_acompletion():
    with openai_vcr.use_cassette("completion_async.yaml"):
        await openai.Completion.acreate(
            model="curie", prompt="As Descartes said, I think, therefore", temperature=0.8, n=1, max_tokens=150
        )

    from ddtrace.contrib.openai._log import _logs_writer

    _logs_writer.periodic()
    assert 0


"""
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
"""
