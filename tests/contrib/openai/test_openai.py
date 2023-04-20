import os
from typing import List
from typing import Optional

import mock
import openai
import pytest
import vcr

from ddtrace import Pin
from ddtrace import Span
from ddtrace import config
from ddtrace import patch
from ddtrace.contrib.openai.patch import unpatch
from ddtrace.filters import TraceFilter
from tests.utils import DummyTracer


# VCR is used to capture and store network requests made to OpenAI.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.
# To (re)-generate the cassettes: pass a real OpenAI API key with
# OPENAI_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
openai.api_key = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
if os.getenv("OPENAI_ORGANIZATION"):
    openai.organization = os.getenv("OPENAI_ORGANIZATION")
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


@pytest.fixture(scope="session")
def mock_metrics():
    patcher = mock.patch("ddtrace.contrib.openai._patch.get_dogstatsd_client")
    DogStatsdMock = patcher.start()
    m = mock.MagicMock()
    DogStatsdMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_logs(scope="session"):
    patcher = mock.patch("ddtrace.contrib.openai._patch.V2LogWriter")
    V2LogWriterMock = patcher.start()
    m = mock.MagicMock()
    V2LogWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def patch_openai():
    patch(openai=True)
    yield
    unpatch()


@pytest.fixture
def snapshot_tracer(patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.fixture
def mock_tracer(patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    mock_tracer = DummyTracer()
    pin.override(openai, tracer=DummyTracer())
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield mock_tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_completion(mock_metrics, mock_logs, snapshot_tracer):
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)

    assert mock_metrics.mock_calls
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                2,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:ada",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
            mock.call.distribution(
                "tokens.completion",
                12,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:ada",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
            mock.call.distribution(
                "tokens.total",
                14,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:ada",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
            mock.call.distribution(
                "request.duration",
                mock.ANY,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:ada",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
async def test_acompletion(mock_metrics, mock_logs, snapshot_tracer):
    with openai_vcr.use_cassette("completion_async.yaml"):
        await openai.Completion.acreate(
            model="curie", prompt="As Descartes said, I think, therefore", temperature=0.8, n=1, max_tokens=150
        )

    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                10,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:curie",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
            mock.call.distribution(
                "tokens.completion",
                150,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:curie",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
            mock.call.distribution(
                "tokens.total",
                160,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:curie",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
            mock.call.distribution(
                "request.duration",
                mock.ANY,
                tags=[
                    "version:None",
                    "env:None",
                    "service:None",
                    "model:curie",
                    "endpoint:completions",
                    "organization.id:None",
                    "organization.name:datadog-4",
                    "error:0",
                ],
            ),
        ]
    )

    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "message": mock.ANY,
                    "hostname": mock.ANY,
                    "ddsource": "openai",
                    "service": None,
                    "status": "info",
                    "ddtags": "env:None,version:None,endpoint:completions,model:curie",
                    "dd.trace_id": mock.ANY,
                    "dd.span_id": mock.ANY,
                    "prompt": "As Descartes said, I think, therefore",
                    "choices": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(
    not hasattr(openai, "ChatCompletion"), reason="ChatCompletion not supported for this version of openai"
)
def test_chat_completion(snapshot_tracer):
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
async def test_achat_completion(snapshot_tracer):
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
async def test_aembedding(snapshot_tracer):
    with openai_vcr.use_cassette("embedding_async.yaml"):
        await openai.Embedding.acreate(input="hello world", model="text-embedding-ada-002")


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(not hasattr(openai, "Moderation"), reason="moderation not supported for this version of openai")
def test_unsupported(snapshot_tracer):
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


@pytest.mark.snapshot(ignores=["meta.http.useragent", "meta.error.stack"])
def test_completion_stream():
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", stream=True)


@pytest.mark.snapshot(ignores=["meta.http.useragent", "meta.error.stack"])
@pytest.mark.skipif(
    not hasattr(openai, "ChatCompletion"), reason="Chat completion not supported for this version of openai"
)
def test_chat_completion_stream():
    with openai_vcr.use_cassette("chat_completion_streamed.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Who won the world series in 2020?"},
            ],
            stream=True,
        )


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
        openai.Completion.create(model="ada", prompt="hello world")


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
            await openai.Completion.acreate(model="ada", prompt="hello world")

    asyncio.run(task())


@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize={"DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE": ["0", "0.25", "0.75", "1"]},
)
def test_completion_sample():
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for completions endpoint"""
    import os

    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import openai_vcr
    from tests.utils import DummyTracer

    ddtrace.Pin().override(openai, tracer=DummyTracer())

    num_completions = 100

    for _ in range(num_completions):
        with openai_vcr.use_cassette("completion_sample_rate.yaml"):
            openai.Completion.create(model="ada", prompt="hello world")

    pin = ddtrace.Pin.get_from(openai)
    traces = pin.tracer.pop_traces()
    sampled = 0
    assert len(traces) == 100, len(traces)
    for trace in traces:
        for span in trace:
            if span.get_tag("response.choices.0.text"):
                sampled += 1
    if ddtrace.config.openai["span_prompt_completion_sample_rate"] == 0:
        assert sampled == 0
    elif ddtrace.config.openai["span_prompt_completion_sample_rate"] == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 15) < sampled < (rate + 15)


@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize={"DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE": ["0", "0.25", "0.75", "1"]},
)
@pytest.mark.skipif(
    not hasattr(openai, "ChatCompletion"), reason="ChatCompletion not supported for this version of openai"
)
def test_chat_completion_sample():
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for chat completions endpoint"""
    import os

    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import openai_vcr
    from tests.utils import DummyTracer

    ddtrace.Pin().override(openai, tracer=DummyTracer())

    num_completions = 100

    for _ in range(num_completions):
        with openai_vcr.use_cassette("chat_completion_sample_rate.yaml"):
            openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "what is your name?"},
                ],
            )

    pin = ddtrace.Pin.get_from(openai)
    traces = pin.tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions
    for trace in traces:
        for span in trace:
            if span.get_tag("response.choices.0.message.role"):
                sampled += 1
    if ddtrace.config.openai["span_prompt_completion_sample_rate"] == 0:
        assert sampled == 0
    elif ddtrace.config.openai["span_prompt_completion_sample_rate"] == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 15) < sampled < (rate + 15)


@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize={"DD_OPENAI_TRUNCATION_THRESHOLD": ["0", "10", "10000"]},
)
def test_completion_truncation():
    """Test functionality of DD_OPENAI_TRUNCATION_THRESHOLD for completions"""
    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import openai_vcr
    from tests.utils import DummyTracer

    ddtrace.Pin().override(openai, tracer=DummyTracer())

    prompt = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10"

    with openai_vcr.use_cassette("completion_truncation.yaml"):
        openai.Completion.create(model="ada", prompt=prompt)

    pin = ddtrace.Pin.get_from(openai)
    traces = pin.tracer.pop_traces()
    assert len(traces) == 1

    for trace in traces:
        for span in trace:
            limit = ddtrace.config.openai["truncation_threshold"]
            prompt = span.get_tag("request.prompt")
            completion = span.get_tag("response.choices.0.text")
            # +3 for the ellipsis
            assert len(prompt) <= limit + 3
            assert len(completion) <= limit + 3
            if "..." in prompt:
                assert len(prompt.replace("...", "")) == limit
            if "..." in completion:
                assert len(completion.replace("...", "")) == limit


@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize={"DD_OPENAI_TRUNCATION_THRESHOLD": ["0", "10", "10000"]},
)
@pytest.mark.skipif(
    not hasattr(openai, "ChatCompletion"), reason="ChatCompletion not supported for this version of openai"
)
def test_chat_completion_truncation():
    """Test functionality of DD_OPENAI_TRUNCATION_THRESHOLD for chat completions"""
    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import openai_vcr
    from tests.utils import DummyTracer

    ddtrace.Pin().override(openai, tracer=DummyTracer())

    with openai_vcr.use_cassette("chat_completion_truncation.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Count from 1 to 100"},
            ],
        )

    pin = ddtrace.Pin.get_from(openai)
    traces = pin.tracer.pop_traces()
    assert len(traces) == 1

    for trace in traces:
        for span in trace:
            limit = ddtrace.config.openai["truncation_threshold"]
            prompt = span.get_tag("request.messages.0.content")
            completion = span.get_tag("response.choices.0.message.content")
            assert len(prompt) <= limit + 3
            assert len(completion) <= limit + 3
            if "..." in prompt:
                assert len(prompt.replace("...", "")) == limit
            if "<TRUNC>" in completion:
                assert len(completion.replace("...", "")) == limit


def test_reconfigure_patch(snapshot_tracer):
    """Ensure new configuration is applied when patch() is called again."""
    old_val = config.openai.span_prompt_completion_sample_rate
    assert old_val != 0.22
    config.openai.span_prompt_completion_sample_rate = 0.22
    patch(openai=True)
    from ddtrace.contrib.openai.patch import _integration

    assert _integration._span_pc_sampler.sample_rate == 0.22
    config.openai.span_prompt_completion_sample_rate = old_val


@pytest.mark.subprocess(
    parametrize={
        "DD_OPENAI_LOGS_ENABLED": ["False", "True", ""],
        "DD_OPENAI_LOG_PROMPT_COMPLETION_SAMPLE_RATE": ["0", "0.25", "0.75", "1"],
    },
)
def test_logs():
    import mock

    import ddtrace

    @mock.patch("ddtrace.contrib.openai._logging.V2LogWriter.enqueue")
    def _test_logs(mock_log):
        import openai

        from tests.contrib.openai.test_openai import openai_vcr

        ddtrace.patch(openai=True)
        total_calls = 100
        for _ in range(total_calls):
            with openai_vcr.use_cassette("completion.yaml"):
                openai.Completion.create(
                    model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10
                )
        if not ddtrace.config.openai["logs_enabled"]:
            # if logging is disabled, mock log should never be called
            mock_log.assert_not_called()
        else:
            logs = mock_log.call_count
            if ddtrace.config.openai["log_prompt_completion_sample_rate"] == 0:
                assert logs == 0
            elif ddtrace.config.openai["log_prompt_completion_sample_rate"] == 1:
                assert logs == total_calls
            else:
                rate = ddtrace.config.openai["log_prompt_completion_sample_rate"] * total_calls
                assert (rate - 15) < logs < (rate + 15)

    _test_logs()
