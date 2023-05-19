import os
import sys
from typing import AsyncGenerator
from typing import Generator
from typing import List
from typing import Optional

import mock
import pytest
import vcr

import ddtrace
from ddtrace import Pin
from ddtrace import Span
from ddtrace import patch
from ddtrace.contrib.openai import _patch
from ddtrace.contrib.openai.patch import unpatch
from ddtrace.filters import TraceFilter
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config


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
def get_openai_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@pytest.fixture(scope="session")
def openai_vcr():
    yield get_openai_vcr()


@pytest.fixture
def api_key_in_env():
    return True


@pytest.fixture
def request_api_key(api_key_in_env, openai_api_key):
    """
    OpenAI allows both using an env var or a specified param for the API key, so this fixture specifies the API key
    (or None) to be used in the actual request param. If the API key is set as an env var, this should return None
    to make sure the env var will be used.
    """
    if api_key_in_env:
        return None
    return openai_api_key


@pytest.fixture
def openai_api_key():
    return os.getenv("OPENAI_API_KEY", "<not-a-real-key>")


@pytest.fixture
def openai_organization():
    return None


@pytest.fixture
def openai(openai_api_key, openai_organization, api_key_in_env):
    import openai

    if api_key_in_env:
        openai.api_key = openai_api_key
    openai.organization = openai_organization
    yield openai
    # Since unpatching doesn't work (see the unpatch() function),
    # wipe out all the OpenAI modules so that state is reset for each test case.
    mods = list(k for k in sys.modules.keys() if k.startswith("openai"))
    for m in mods:
        del sys.modules[m]


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
    """
    Note that this fixture must be ordered BEFORE mock_tracer as it needs to patch the log writer
    before it is instantiated.
    """
    patcher = mock.patch("ddtrace.contrib.openai._patch.V2LogWriter")
    V2LogWriterMock = patcher.start()
    m = mock.MagicMock()
    V2LogWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def ddtrace_config_openai():
    config = {}
    return config


@pytest.fixture
def patch_openai(ddtrace_config_openai, openai_api_key, openai_organization, api_key_in_env):
    with override_config("openai", ddtrace_config_openai):
        if api_key_in_env:
            openai.api_key = openai_api_key
        openai.organization = openai_organization
        patch(openai=True)
        yield
        unpatch()


@pytest.fixture
def snapshot_tracer(openai, patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield pin.tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.fixture
def mock_tracer(openai, patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin.override(openai, tracer=mock_tracer)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield mock_tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.mark.parametrize("ddtrace_config_openai", [dict(metrics_enabled=True), dict(metrics_enabled=False)])
def test_config(ddtrace_config_openai, mock_tracer, openai):
    # Ensure that the module state is reloaded for each test run
    assert not hasattr(openai, "_test")
    openai._test = 1

    # Ensure overriding the config works
    assert ddtrace.config.openai.metrics_enabled is ddtrace_config_openai["metrics_enabled"]


def iswrapped(obj):
    return hasattr(obj, "__dd_wrapped__")


def test_patching(openai):
    """Ensure that the correct objects are patched and not double patched."""

    # for some reason these can't be specified as the real python objects...
    # no clue why (eg. openai.Completion.create doesn't work)
    methods = [
        (openai.Completion, "create"),
        (openai.api_resources.completion.Completion, "create"),
        (openai.Completion, "acreate"),
        (openai.api_resources.completion.Completion, "acreate"),
        (openai.api_requestor, "_make_session"),
        (openai.util, "convert_to_openai_object"),
        (openai.Embedding, "create"),
        (openai.Embedding, "acreate"),
    ]
    if hasattr(openai, "ChatCompletion"):
        methods += [
            (openai.ChatCompletion, "create"),
            (openai.api_resources.chat_completion.ChatCompletion, "create"),
            (openai.ChatCompletion, "acreate"),
            (openai.api_resources.chat_completion.ChatCompletion, "acreate"),
        ]

    for m in methods:
        assert not iswrapped(getattr(m[0], m[1]))

    patch(openai=True)
    for m in methods:
        assert iswrapped(getattr(m[0], m[1]))

    # Ensure double patching does not occur
    patch(openai=True)
    for m in methods:
        assert not iswrapped(getattr(m[0], m[1]).__dd_wrapped__)


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_completion(api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, snapshot_tracer):
    with openai_vcr.use_cassette("completion.yaml"):
        resp = openai.Completion.create(
            api_key=request_api_key, model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10
        )

    assert resp["object"] == "text_completion"
    assert resp["model"] == "ada"
    assert resp["choices"] == [
        {"finish_reason": "length", "index": 0, "logprobs": None, "text": ", relax!” I said to my laptop"},
        {"finish_reason": "stop", "index": 1, "logprobs": None, "text": " (1"},
    ]

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.model:ada",
        "openai.endpoint:completions",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                2,
                tags=expected_tags + ["openai.estimated:false"],
            ),
            mock.call.distribution(
                "tokens.completion",
                12,
                tags=expected_tags + ["openai.estimated:false"],
            ),
            mock.call.distribution(
                "tokens.total",
                14,
                tags=expected_tags + ["openai.estimated:false"],
            ),
            mock.call.distribution(
                "request.duration",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
        ],
        any_order=True,
    )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_acompletion(
    api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, mock_logs, snapshot_tracer
):
    with openai_vcr.use_cassette("completion_async.yaml"):
        resp = await openai.Completion.acreate(
            api_key=request_api_key,
            model="curie",
            prompt="As Descartes said, I think, therefore",
            temperature=0.8,
            n=1,
            max_tokens=150,
        )
    assert resp["object"] == "text_completion"
    assert resp["choices"] == [
        {
            "finish_reason": "length",
            "index": 0,
            "logprobs": None,
            "text": " I am; and I am in a sense a non-human entity woven together from "
            "memories, desires and emotions. But, who is to say that I am not an "
            "artificial intelligence. The brain is a self-organising, "
            "self-aware, virtual reality computer … so how is it, who exactly is "
            "it, this thing that thinks, feels, loves and believes? Are we not "
            "just software running on hardware?\n"
            "\n"
            "Recently, I have come to take a more holistic view of my identity, "
            "not as a series of fleeting moments, but as a long-term, ongoing "
            "process. The key question for me is not that of ‘who am I?’ but "
            "rather, ‘how am I?’ – a question",
        }
    ]
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.model:curie",
        "openai.endpoint:completions",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                10,
                tags=expected_tags + ["openai.estimated:false"],
            ),
            mock.call.distribution(
                "tokens.completion",
                150,
                tags=expected_tags + ["openai.estimated:false"],
            ),
            mock.call.distribution(
                "tokens.total",
                160,
                tags=expected_tags + ["openai.estimated:false"],
            ),
            mock.call.distribution(
                "request.duration",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
        ],
        any_order=True,
    )
    mock_logs.assert_not_called()


@pytest.mark.xfail(reason="An API key is required when logs are enabled")
@pytest.mark.parametrize("ddtrace_config_openai", [dict(_api_key="", logs_enabled=True)])
def test_logs_no_api_key(openai, ddtrace_config_openai, mock_tracer):
    """When no DD_API_KEY is set, the patching fails"""
    pass


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [
        # Default service, env, version
        dict(
            _api_key="<not-real-but-it's-something>",
            logs_enabled=True,
            log_prompt_completion_sample_rate=1.0,
        ),
    ],
)
def test_logs_completions(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for completion endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.assert_has_calls(
        [
            mock.call.start(),
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": mock.ANY,
                    "hostname": mock.ANY,
                    "ddsource": "openai",
                    "service": "",
                    "status": "info",
                    "ddtags": "env:,version:,openai.endpoint:completions,openai.model:ada,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>",  # noqa: E501
                    "dd.trace_id": str(trace_id),
                    "dd.span_id": str(span_id),
                    "prompt": "Hello world",
                    "choices": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [dict(_api_key="<not-real-but-it's-something>", logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_global_tags(openai_vcr, ddtrace_config_openai, openai, mock_metrics, mock_logs, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data

    All data should also be tagged with the same OpenAI data.
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with openai_vcr.use_cassette("completion.yaml"):
            openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)

    span = mock_tracer.pop_traces()[0][0]
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("openai.model") == "ada"
    assert span.get_tag("openai.endpoint") == "completions"
    assert span.get_tag("openai.organization.name") == "datadog-4"
    assert span.get_tag("openai.user.api_key") == "sk-...key>"

    for _, args, kwargs in mock_metrics.mock_calls:
        expected_metrics = [
            "service:test-svc",
            "env:staging",
            "version:1234",
            "openai.model:ada",
            "openai.endpoint:completions",
            "openai.organization.name:datadog-4",
            "openai.user.api_key:sk-...key>",
        ]
        actual_tags = kwargs.get("tags")
        for m in expected_metrics:
            assert m in actual_tags

    for call, args, kwargs in mock_logs.mock_calls:
        if call != "enqueue":
            continue
        log = args[0]
        assert log["service"] == "test-svc"
        assert (
            log["ddtags"]
            == "env:staging,version:1234,openai.endpoint:completions,openai.model:ada,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>"  # noqa: E501
        )


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_chat_completion(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    with openai_vcr.use_cassette("chat_completion.yaml"):
        openai.ChatCompletion.create(
            api_key=request_api_key,
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


@pytest.mark.parametrize("ddtrace_config_openai", [dict(metrics_enabled=b) for b in [True, False]])
def test_enable_metrics(openai, openai_vcr, ddtrace_config_openai, mock_metrics, mock_tracer):
    """Ensure the metrics_enabled configuration works."""
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)
    if ddtrace_config_openai["metrics_enabled"]:
        assert mock_metrics.mock_calls
    else:
        assert not mock_metrics.mock_calls


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_achat_completion(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with openai_vcr.use_cassette("chat_completion_async.yaml"):
        await openai.ChatCompletion.acreate(
            api_key=request_api_key,
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
@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_embedding(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(api_key=request_api_key, input="hello world", model="text-embedding-ada-002")


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_embedding_string_array(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(input=["hello world", "hello again"], model="text-embedding-ada-002")


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_embedding_token_array(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(input=[1111, 2222, 3333], model="text-embedding-ada-002")


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_embedding_array_of_token_arrays(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(
            input=[[1111, 2222, 3333], [4444, 5555, 6666], [7777, 8888, 9999]], model="text-embedding-ada-002"
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_aembedding(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding_async.yaml"):
        await openai.Embedding.acreate(api_key=request_api_key, input="hello world", model="text-embedding-ada-002")


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_unsupported(openai, openai_vcr, snapshot_tracer):
    # no openai spans expected
    with openai_vcr.use_cassette("moderation.yaml"):
        openai.Moderation.create(
            input="Here is some perfectly innocuous text that follows all OpenAI content policies."
        )


@pytest.mark.snapshot(ignores=["meta.http.useragent", "meta.error.stack"])
def test_misuse(openai, snapshot_tracer):
    with pytest.raises(openai.error.InvalidRequestError):
        openai.Completion.create(input="wrong arg")


def test_completion_stream(openai, openai_vcr, mock_metrics, mock_tracer):
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        resp = openai.Completion.create(model="ada", prompt="Hello world", stream=True)
        assert isinstance(resp, Generator)
        chunks = [c for c in resp]

    completion = "".join([c["choices"][0]["text"] for c in chunks])
    assert completion == '! ... A page layouts page drawer? ... Interesting. The "Tools" is'

    traces = mock_tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.model:ada",
        "openai.endpoint:completions",
        "openai.organization.id:",
        "openai.organization.name:user-f23xvdxbrssd56y1ghcjdcue",
        "openai.user.api_key:sk-...key>",
        "error:0",
        "openai.estimated:true",
    ]
    assert mock.call.distribution("tokens.prompt", 2, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", len(chunks), tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", len(chunks) + 2, tags=expected_tags) in mock_metrics.mock_calls


@pytest.mark.asyncio
async def test_completion_async_stream(openai, openai_vcr, mock_metrics, mock_tracer):
    with openai_vcr.use_cassette("completion_async_streamed.yaml"):
        resp = await openai.Completion.acreate(model="ada", prompt="Hello world", stream=True)
        assert isinstance(resp, AsyncGenerator)
        chunks = [c async for c in resp]

    completion = "".join([c["choices"][0]["text"] for c in chunks])
    assert completion == "\" and just start creating stuff. Don't expect it to draw like this."

    traces = mock_tracer.pop_traces()
    assert len(traces) == 1

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.model:ada",
        "openai.endpoint:completions",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
        "openai.estimated:true",
    ]
    assert mock.call.distribution("tokens.prompt", 2, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", len(chunks), tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", len(chunks) + 2, tags=expected_tags) in mock_metrics.mock_calls


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_chat_completion_stream(openai, openai_vcr, mock_metrics, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    with openai_vcr.use_cassette("chat_completion_streamed.yaml"):
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Who won the world series in 2020?"},
            ],
            stream=True,
        )
        span = snapshot_tracer.current_span()
        chunks = [c for c in resp]
        assert len(chunks) == 15
        completion = "".join([c["choices"][0]["delta"].get("content", "") for c in chunks])
        assert completion == "The Los Angeles Dodgers won the World Series in 2020."

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.model:gpt-3.5-turbo",
        "openai.endpoint:chat.completions",
        "openai.organization.id:",
        "openai.organization.name:user-f23xvdxbrssd56y1ghcjdcue",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    assert mock.call.distribution("request.duration", span.duration_ns, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.requests", "3", tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.remaining.requests", "2", tags=expected_tags) in mock_metrics.mock_calls
    expected_tags += ["openai.estimated:true"]
    assert mock.call.distribution("tokens.prompt", 8, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", len(chunks), tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", len(chunks) + 8, tags=expected_tags) in mock_metrics.mock_calls


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.asyncio
async def test_chat_completion_async_stream(openai, openai_vcr, mock_metrics, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    with openai_vcr.use_cassette("chat_completion_streamed_async.yaml"):
        resp = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Who is the captain of the toronto maple leafs?"},
            ],
            stream=True,
        )
        span = snapshot_tracer.current_span()
        chunks = [c async for c in resp]
        assert len(chunks) == 39
        completion = "".join([c["choices"][0]["delta"].get("content", "") for c in chunks])
        assert (
            completion
            == "As an AI language model, I do not have access to real-time information but as of the 2021 season, the captain of the Toronto Maple Leafs is John Tavares."  # noqa: E501
        )

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.model:gpt-3.5-turbo",
        "openai.endpoint:chat.completions",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    assert mock.call.distribution("request.duration", span.duration_ns, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.requests", "3500", tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.tokens", "90000", tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.remaining.requests", "3499", tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.remaining.tokens", "89971", tags=expected_tags) in mock_metrics.mock_calls
    expected_tags += ["openai.estimated:true"]
    assert mock.call.distribution("tokens.prompt", 10, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", len(chunks), tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", len(chunks) + 10, tags=expected_tags) in mock_metrics.mock_calls


@pytest.mark.snapshot(ignores=["meta.http.useragent"], async_mode=False)
def test_integration_sync(ddtrace_run_python_code_in_subprocess):
    """OpenAI uses requests for its synchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and requests spans.
    """
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "<not-real>"),
            "PYTHONPATH": ":".join(pypath),
            # Disable metrics because the test agent doesn't support metrics
            "DD_OPENAI_METRICS_ENABLED": "false",
        }
    )
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import openai
import ddtrace
from tests.contrib.openai.test_openai import FilterOrg, get_openai_vcr
pin = ddtrace.Pin.get_from(openai)
pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})
with get_openai_vcr().use_cassette("completion_2.yaml"):
    resp = openai.Completion.create(model="ada", prompt="hello world")
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.snapshot(ignores=["meta.http.useragent"], async_mode=False)
def test_integration_async(ddtrace_run_python_code_in_subprocess):
    """OpenAI uses requests for its synchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and requests spans.

    FIXME: there _should_ be aiohttp spans generated for this test case. There aren't
           because the patching VCR does into aiohttp interferes with the tracing patching.
    """
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "<not-real>"),
            "PYTHONPATH": ":".join(pypath),
            # Disable metrics because the test agent doesn't support metrics
            "DD_OPENAI_METRICS_ENABLED": "false",
        }
    )
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import asyncio
import openai
import ddtrace
from tests.contrib.openai.test_openai import FilterOrg, get_openai_vcr
pin = ddtrace.Pin.get_from(openai)
pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})
async def task():
    with get_openai_vcr().use_cassette("acompletion_2.yaml"):
        resp = await openai.Completion.acreate(model="ada", prompt="hello world")

asyncio.run(task())
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [dict(span_prompt_completion_sample_rate=r) for r in [0, 0.25, 0.75, 1]],
)
def test_completion_sample(openai, openai_vcr, ddtrace_config_openai, mock_tracer):
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for completions endpoint"""
    num_completions = 200

    for _ in range(num_completions):
        with openai_vcr.use_cassette("completion_sample_rate.yaml"):
            openai.Completion.create(model="ada", prompt="hello world")

    traces = mock_tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions, len(traces)
    for trace in traces:
        for span in trace:
            if span.get_tag("openai.response.choices.0.text"):
                sampled += 1
    if ddtrace.config.openai.span_prompt_completion_sample_rate == 0:
        assert sampled == 0
    elif ddtrace.config.openai.span_prompt_completion_sample_rate == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 30) < sampled < (rate + 30)


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [dict(span_prompt_completion_sample_rate=r) for r in [0, 0.25, 0.75, 1]],
)
def test_chat_completion_sample(openai, openai_vcr, ddtrace_config_openai, mock_tracer):
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for chat completions endpoint"""
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    num_completions = 200

    for _ in range(num_completions):
        with openai_vcr.use_cassette("chat_completion_sample_rate.yaml"):
            openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "what is your name?"},
                ],
            )

    traces = mock_tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions
    for trace in traces:
        for span in trace:
            if span.get_tag("openai.response.choices.0.message.role"):
                sampled += 1
    if ddtrace.config.openai["span_prompt_completion_sample_rate"] == 0:
        assert sampled == 0
    elif ddtrace.config.openai["span_prompt_completion_sample_rate"] == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 30) < sampled < (rate + 30)


@pytest.mark.parametrize("ddtrace_config_openai", [dict(truncation_threshold=t) for t in [0, 10, 10000]])
def test_completion_truncation(openai, openai_vcr, mock_tracer):
    """Test functionality of DD_OPENAI_TRUNCATION_THRESHOLD for completions"""
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    prompt = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10"

    with openai_vcr.use_cassette("completion_truncation.yaml"):
        openai.Completion.create(model="ada", prompt=prompt)

    with openai_vcr.use_cassette("chat_completion_truncation.yaml"):
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Count from 1 to 100"},
            ],
        )
        assert resp["choices"] == [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, "
                    "16, 17, 18, 19, 20,\n"
                    "21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, "
                    "34, 35, 36, 37, 38, 39, 40,\n"
                    "41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, "
                    "54, 55, 56, 57, 58, 59, 60,\n"
                    "61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, "
                    "74, 75, 76, 77, 78, 79, 80,\n"
                    "81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, "
                    "94, 95, 96, 97, 98, 99, 100.",
                    "role": "assistant",
                },
            }
        ]

    traces = mock_tracer.pop_traces()
    assert len(traces) == 2

    limit = ddtrace.config.openai["span_char_limit"]
    for trace in traces:
        for span in trace:
            if span.get_tag("openai.endpoint") == "completions":
                prompt = span.get_tag("openai.request.prompt")
                completion = span.get_tag("openai.response.choices.0.text")
                # +3 for the ellipsis
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit
            else:
                prompt = span.get_tag("openai.request.messages.0.content")
                completion = span.get_tag("openai.response.choices.0.message.content")
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [
        dict(
            _api_key="<not-real-but-it's-something>",
            logs_enabled=True,
            log_prompt_completion_sample_rate=r,
        )
        for r in [0, 0.25, 0.75, 1]
    ],
)
def test_logs_sample_rate(openai, openai_vcr, ddtrace_config_openai, mock_logs, mock_tracer):
    total_calls = 200
    for _ in range(total_calls):
        with openai_vcr.use_cassette("completion.yaml"):
            openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)

    logs = mock_logs.enqueue.call_count
    if ddtrace.config.openai["log_prompt_completion_sample_rate"] == 0:
        assert logs == 0
    elif ddtrace.config.openai["log_prompt_completion_sample_rate"] == 1:
        assert logs == total_calls
    else:
        rate = ddtrace.config.openai["log_prompt_completion_sample_rate"] * total_calls
        assert (rate - 30) < logs < (rate + 30)


def test_est_tokens():
    """Oracle numbers are from https://platform.openai.com/tokenizer (GPT-3)."""
    est = _patch._est_tokens
    assert est("") == 0  # oracle: 1
    assert est("hello") == 1  # oracle: 1
    assert est("hello, world") == 3  # oracle: 3
    assert est("hello world") == 2  # oracle: 2
    assert est("Hello world, how are you?") == 6  # oracle: 7
    assert est("    hello    ") == 3  # oracle: 8
    assert (
        est(
            "The GPT family of models process text using tokens, which are common sequences of characters found in text. The models understand the statistical relationships between these tokens, and excel at producing the next token in a sequence of tokens."  # noqa E501
        )
        == 54
    )  # oracle: 44
    assert (
        est(
            "You can use the tool below to understand how a piece of text would be tokenized by the API, and the total count of tokens in that piece of text."  # noqa: E501
        )
        == 33
    )  # oracle: 33
    assert (
        est(
            "A helpful rule of thumb is that one token generally corresponds to ~4 characters of text for common "
            "English text. This translates to roughly ¾ of a word (so 100 tokens ~= 75 words). If you need a "
            "programmatic interface for tokenizing text, check out our tiktoken package for Python. For JavaScript, "
            "the gpt-3-encoder package for node.js works for most GPT-3 models."
        )
        == 83
    )  # oracle: 87

    # Expected to be a disparity since our assumption is based on english words
    assert (
        est(
            """Lorem ipsum dolor sit amet, consectetur adipiscing elit. Donec hendrerit sapien eu erat imperdiet, in
 maximus elit malesuada. Pellentesque quis gravida purus. Nullam eu eros vitae dui placerat viverra quis a magna. Mauris
 vitae lorem quis neque pharetra congue. Praesent volutpat dui eget nibh auctor, sit amet elementum velit faucibus.
 Nullam ultricies dolor sit amet nisl molestie, a porta metus suscipit. Vivamus eget luctus mauris. Proin commodo
 elementum ex a pretium. Nam vitae ipsum sed dolor congue fermentum. Sed quis bibendum sapien, dictum venenatis urna.
 Morbi molestie lacinia iaculis. Proin lorem mauris, interdum eget lectus a, auctor volutpat nisl. Suspendisse ac
 tincidunt sapien. Cras congue ipsum sit amet congue ullamcorper. Proin hendrerit at erat vulputate consequat."""
        )
        == 175
    )  # oracle 281

    assert (
        est(
            "I want you to act as a linux terminal. I will type commands and you will reply with what the terminal should show. I want you to only reply with the terminal output inside one unique code block, and nothing else. do not write explanations. do not type commands unless I instruct you to do so. When I need to tell you something in English, I will do so by putting text inside curly brackets {like this}. My first command is pwd"  # noqa: E501
        )
        == 97
    )  # oracle: 92
