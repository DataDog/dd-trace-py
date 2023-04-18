import os
from typing import List
from typing import Optional

import openai
import pytest
import vcr

from ddtrace import Pin
from ddtrace import Span
from ddtrace import patch
from ddtrace.contrib.openai.patch import unpatch
from ddtrace.filters import TraceFilter


# VCR is used to capture and store network requests made to OpenAI.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.
# To (re)-generate the cassettes: pass a real OpenAI API key with
# OPENAI_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check the generated cassettes so they don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
openai.api_key = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
openai.organization = os.getenv("OPENAI_ORGANIZATION", "<not-a-real-org>")
openai_vcr = vcr.VCR(
    cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
    record_mode="once",
    match_on=["path"],
    filter_headers=["authorization", "OpenAI-Organization"],
    # Ignore requests to the agent
    ignore_localhost=True,
)


class FilterOrg(TraceFilter):
    """Replace the organization tag on spans with fake data."""

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        for span in trace:
            if span.get_tag("organization"):
                span.set_tag_str("organization", "not-a-real-org")
        return trace


@pytest.fixture(autouse=True)
def patch_openai():
    patch(openai=True)
    pin = Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})
    yield
    unpatch()

    # Force a flush of the logs for the given test case
    from ddtrace.contrib.openai._log import _logs_writer

    _logs_writer.periodic()


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_completion():
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
async def test_acompletion():
    with openai_vcr.use_cassette("completion_async.yaml"):
        await openai.Completion.acreate(
            model="curie", prompt="As Descartes said, I think, therefore", temperature=0.8, n=1, max_tokens=150
        )


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
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


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(
    not hasattr(openai, "ChatCompletion"), reason="ChatCompletion not supported for this version of openai"
)
async def test_achat_completion():
    with openai_vcr.use_cassette("chat_completion_async.yaml"):
        await openai.ChatCompletion.acreate(
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


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(
    not hasattr(openai, "Embedding"),
    reason="embedding not supported for this version of openai",
)
def test_embedding():
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(input="hello world", model="text-embedding-ada-002")


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(
    not hasattr(openai, "Embedding"),
    reason="embedding not supported for this version of openai",
)
async def test_aembedding():
    with openai_vcr.use_cassette("embedding_async.yaml"):
        await openai.Embedding.acreate(input="hello world", model="text-embedding-ada-002")


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(not hasattr(openai, "Moderation"), reason="moderation not supported for this version of openai")
def test_unsupported():
    # no openai spans expected
    with openai_vcr.use_cassette("moderation.yaml"):
        openai.Moderation.create(
            input="Here is some perfectly innocuous text that follows all OpenAI content policies."
        )


@pytest.mark.snapshot(ignores=["meta.http.useragent", "meta.error.stack"])
@pytest.mark.skipif(not hasattr(openai, "Completion"), reason="completion not supported for this version of openai")
def test_misuse():
    try:
        openai.Completion.create(input="wrong arg")
    except openai.error.InvalidRequestError:
        # this error is expected
        pass


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.subprocess(ddtrace_run=True)
def test_integration_sync():
    """OpenAI uses requests for its synchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and requests spans.
    """
    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import FilterOrg
    from tests.contrib.openai.test_openai import openai_vcr

    pin = ddtrace.Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    with openai_vcr.use_cassette("completion_2.yaml"):
        openai.Completion.create(
            model="ada",
            prompt="hello world"
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.subprocess(ddtrace_run=True)
# FIXME: 'aiohttp.request', 'TCPConnector.connect' on second 
# run of the test, might do with cassettes
def test_integration_async():
    """OpenAI uses requests for its synchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and requests spans.
    """
    import asyncio

    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import FilterOrg
    from tests.contrib.openai.test_openai import openai_vcr

    pin = ddtrace.Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    async def task():
        with openai_vcr.use_cassette("acompletion_2.yaml"):
            await openai.Completion.acreate(
                model="ada",
                prompt="hello world"
            )
    asyncio.run(task())


@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize={"DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE": ["0", "0.25", "0.75", "1"]},
)
def test_completion_sample():
    """OpenAI uses requests for its synchronous requests.
    """
    import os
    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import openai_vcr
    from tests.utils import DummyTracer

    ddtrace.Pin().override(openai, tracer=DummyTracer())

    num_completions = 100

    for _ in range(num_completions):
        with openai_vcr.use_cassette("completion_sample_rate.yaml"):
            openai.Completion.create(
                model="ada",
                prompt="hello world"
            )

    pin = ddtrace.Pin.get_from(openai)
    traces = pin.tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions
    for trace in traces:
        for span in trace:
            if span.get_tag("response.choices.0.text"):
                sampled += 1
    if os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE") == "0":
        assert sampled == 0
    elif os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE") == "1":
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = float(os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE")) * num_completions
        assert (rate - 15) < sampled < (rate + 15)

@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize={"DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE": ["0", "0.25", "0.75", "1"]},
)
@pytest.mark.skipif(
    not hasattr(openai, "ChatCompletion"), reason="ChatCompletion not supported for this version of openai"
)
def test_chat_completion_sample():
    """OpenAI uses requests for its synchronous requests.
    """
    import os
    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import openai_vcr
    from tests.utils import DummyTracer

    ddtrace.Pin().override(openai, tracer=DummyTracer())

    num_completions = 100

    for _ in range(num_completions):
        with openai_vcr.use_cassette("chat_completion_sample_rate.yaml"):
            openai.ChatCompletion.acreate(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "what is your name?"},
                ]
            )

    pin = ddtrace.Pin.get_from(openai)
    traces = pin.tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions
    for trace in traces:
        for span in trace:
            if span.get_tag("response.choices.0.message.role"):
                sampled += 1
    if os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE") == "0":
        assert sampled == 0
    elif os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE") == "1":
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = float(os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE")) * num_completions
        assert (rate - 15) < sampled < (rate + 15)