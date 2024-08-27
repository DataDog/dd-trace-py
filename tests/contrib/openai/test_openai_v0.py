from io import BytesIO
import os
from typing import AsyncGenerator
from typing import Generator

import mock
import openai as openai_module
from PIL import Image
import pytest

import ddtrace
from ddtrace import patch
from ddtrace.contrib.openai.utils import _est_tokens
from ddtrace.internal.utils.version import parse_version
from tests.contrib.openai.utils import chat_completion_custom_functions
from tests.contrib.openai.utils import chat_completion_input_description
from tests.contrib.openai.utils import get_openai_vcr
from tests.contrib.openai.utils import iswrapped
from tests.utils import override_global_config
from tests.utils import snapshot_context


TIKTOKEN_AVAILABLE = os.getenv("TIKTOKEN_AVAILABLE", False)
pytestmark = pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 0, 0), reason="This module only tests openai < 1.0"
)


@pytest.fixture(scope="session")
def openai_vcr():
    yield get_openai_vcr(subdirectory_name="v0")


@pytest.mark.parametrize("ddtrace_config_openai", [dict(metrics_enabled=True), dict(metrics_enabled=False)])
def test_config(ddtrace_config_openai, mock_tracer, openai):
    # Ensure that the module state is reloaded for each test run
    assert not hasattr(openai, "_test")
    openai._test = 1

    # Ensure overriding the config works
    assert ddtrace.config.openai.metrics_enabled is ddtrace_config_openai["metrics_enabled"]


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


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_model_list(api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_list",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("model_list.yaml"):
            openai.Model.list(api_key=request_api_key, user="ddtrace-test")


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_model_alist(api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_list",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("model_list_async.yaml"):
            await openai.Model.alist(api_key=request_api_key, user="ddtrace-test")


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_model_retrieve(api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_retrieve",
        ignores=["meta.http.useragent", "meta.openai.base_url", "meta.openai.response.created"],
    ):
        with openai_vcr.use_cassette("model_retrieve.yaml"):
            openai.Model.retrieve("curie", api_key=request_api_key, user="ddtrace-test")


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_model_aretrieve(api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_retrieve",
        ignores=["meta.http.useragent", "meta.openai.base_url", "meta.openai.response.created"],
    ):
        with openai_vcr.use_cassette("model_retrieve_async.yaml"):
            await openai.Model.aretrieve("curie", api_key=request_api_key, user="ddtrace-test")


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_completion(
    api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, mock_logs, mock_llmobs_writer, snapshot_tracer
):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_completion",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("completion.yaml"):
            resp = openai.Completion.create(
                api_key=request_api_key,
                model="ada",
                prompt="Hello world",
                temperature=0.8,
                n=2,
                stop=".",
                max_tokens=10,
                user="ddtrace-test",
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
        "openai.request.model:ada",
        "model:ada",
        "openai.request.endpoint:/v1/completions",
        "openai.request.method:POST",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution("tokens.prompt", 2, tags=expected_tags + ["openai.estimated:false"]),
            mock.call.distribution("tokens.completion", 12, tags=expected_tags + ["openai.estimated:false"]),
            mock.call.distribution("tokens.total", 14, tags=expected_tags + ["openai.estimated:false"]),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.remaining.requests", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.requests", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.remaining.tokens", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.tokens", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    mock_logs.start.assert_not_called()
    mock_logs.enqueue.assert_not_called()
    mock_llmobs_writer.start.assert_not_called()
    mock_llmobs_writer.enqueue.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_acompletion(
    api_key_in_env, request_api_key, openai, openai_vcr, mock_metrics, mock_logs, mock_llmobs_writer, snapshot_tracer
):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_acompletion",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("completion_async.yaml"):
            resp = await openai.Completion.acreate(
                api_key=request_api_key,
                model="curie",
                prompt="As Descartes said, I think, therefore",
                temperature=0.8,
                n=1,
                max_tokens=150,
                user="ddtrace-test",
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
        "openai.request.model:curie",
        "model:curie",
        "openai.request.endpoint:/v1/completions",
        "openai.request.method:POST",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution("tokens.prompt", 10, tags=expected_tags + ["openai.estimated:false"]),
            mock.call.distribution("tokens.completion", 150, tags=expected_tags + ["openai.estimated:false"]),
            mock.call.distribution("tokens.total", 160, tags=expected_tags + ["openai.estimated:false"]),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.remaining.requests", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.requests", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.remaining.tokens", mock.ANY, tags=expected_tags),
            mock.call.gauge("ratelimit.tokens", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    mock_logs.start.assert_not_called()
    mock_logs.enqueue.assert_not_called()
    mock_llmobs_writer.start.assert_not_called()
    mock_llmobs_writer.enqueue.assert_not_called()


@pytest.mark.xfail(reason="An API key is required when logs are enabled")
@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True)])
def test_logs_no_api_key(openai, ddtrace_config_openai, mock_tracer):
    """When no DD_API_KEY is set, the patching fails"""
    pass


@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_logs_completions(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for completion endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(
            model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
        )
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
                    "ddtags": "env:,version:,openai.request.endpoint:/v1/completions,openai.request.method:POST,openai.request.model:ada,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>",  # noqa: E501
                    "dd.trace_id": "{:x}".format(trace_id),
                    "dd.span_id": str(span_id),
                    "prompt": "Hello world",
                    "choices": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
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
            openai.Completion.create(
                model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
            )

    span = mock_tracer.pop_traces()[0][0]
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("openai.request.model") == "ada"
    assert span.get_tag("openai.request.endpoint") == "/v1/completions"
    assert span.get_tag("openai.request.method") == "POST"
    assert span.get_tag("openai.organization.name") == "datadog-4"
    assert span.get_tag("openai.user.api_key") == "sk-...key>"

    for _, _args, kwargs in mock_metrics.mock_calls:
        expected_metrics = [
            "service:test-svc",
            "env:staging",
            "version:1234",
            "openai.request.model:ada",
            "model:ada",
            "openai.request.endpoint:/v1/completions",
            "openai.request.method:POST",
            "openai.organization.name:datadog-4",
            "openai.user.api_key:sk-...key>",
        ]
        actual_tags = kwargs.get("tags")
        for m in expected_metrics:
            assert m in actual_tags

    for call, args, _kwargs in mock_logs.mock_calls:
        if call != "enqueue":
            continue
        log = args[0]
        assert log["service"] == "test-svc"
        assert (
            log["ddtags"]
            == "env:staging,version:1234,openai.request.endpoint:/v1/completions,openai.request.method:POST,openai.request.model:ada,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>"  # noqa: E501
        )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_chat_completion(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_chat_completion",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
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
                user="ddtrace-test",
            )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_chat_completion_function_calling",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_chat_completion_function_calling(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with openai_vcr.use_cassette("chat_completion_function_call.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": chat_completion_input_description}],
            functions=chat_completion_custom_functions,
            function_call="auto",
            user="ddtrace-test",
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_chat_completion_function_calling",
    ignores=["meta.http.useragent", "meta.openai.base_url", "meta.openai.response.choices.0.finish_reason"],
)
def test_chat_completion_tool_calling(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with openai_vcr.use_cassette("chat_completion_tool_call.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": chat_completion_input_description}],
            tools=[{"type": "function", "function": chat_completion_custom_functions[0]}],
            tool_choice="auto",
            user="ddtrace-test",
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_chat_completion_image_input",
    ignores=[
        "meta.http.useragent",
        "meta.openai.base_url",
    ],
)
def test_chat_completion_image_input(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    image_url = (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk"
        ".jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
    )
    with openai_vcr.use_cassette("chat_completion_image_input.yaml"):
        openai.ChatCompletion.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What’s in this image?"},
                        {
                            "type": "image_url",
                            "image_url": image_url,
                        },
                    ],
                }
            ],
        )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(metrics_enabled=b) for b in [True, False]])
def test_enable_metrics(openai, openai_vcr, ddtrace_config_openai, mock_metrics, mock_tracer):
    """Ensure the metrics_enabled configuration works."""
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(
            model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
        )
    if ddtrace_config_openai["metrics_enabled"]:
        assert mock_metrics.mock_calls
    else:
        assert not mock_metrics.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_achat_completion(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_chat_completion",
        ignores=["meta.http.useragent", "meta.openai.base_url", "meta._dd.p.tid"],
    ):
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
                user="ddtrace-test",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_image_create(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_create",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("image_create.yaml"):
            openai.Image.create(
                api_key=request_api_key,
                prompt="sleepy capybara with monkey on top",
                n=1,
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_image_acreate(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_create",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("image_create_async.yaml"):
            await openai.Image.acreate(
                api_key=request_api_key,
                prompt="sleepy capybara with monkey on top",
                n=1,
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_logs_image_create(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for image endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with openai_vcr.use_cassette("image_create.yaml"):
        openai.Image.create(
            prompt="sleepy capybara with monkey on top",
            n=1,
            size="256x256",
            response_format="url",
            user="ddtrace-test",
        )
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
                    "ddtags": "env:,version:,openai.request.endpoint:/v1/images/generations,openai.request.method:POST,openai.request.model:dall-e,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>",  # noqa: E501
                    "dd.trace_id": "{:x}".format(trace_id),
                    "dd.span_id": str(span_id),
                    "prompt": "sleepy capybara with monkey on top",
                    "choices": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_image_edit(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_edit",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("image_edit.yaml"):
            openai.Image.create_edit(
                image=open(os.path.join(os.path.dirname(__file__), "test_data/image.png"), "rb"),
                mask=open(os.path.join(os.path.dirname(__file__), "test_data/mask.png"), "rb"),
                api_key=request_api_key,
                n=1,
                prompt="A sunlit indoor lounge area with a pool containing a flamingo",
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_image_aedit(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_edit",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("image_edit_async.yaml"):
            await openai.Image.acreate_edit(
                image=open(os.path.join(os.path.dirname(__file__), "test_data/image.png"), "rb"),
                mask=open(os.path.join(os.path.dirname(__file__), "test_data/mask.png"), "rb"),
                api_key=request_api_key,
                n=1,
                prompt="A sunlit indoor lounge area with a pool containing a flamingo",
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_logs_image_edit(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for image endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with openai_vcr.use_cassette("image_edit.yaml"):
        openai.Image.create_edit(
            image=open(os.path.join(os.path.dirname(__file__), "test_data/image.png"), "rb"),
            mask=open(os.path.join(os.path.dirname(__file__), "test_data/mask.png"), "rb"),
            n=1,
            prompt="A sunlit indoor lounge area with a pool containing a flamingo",
            size="256x256",
            response_format="url",
            user="ddtrace-test",
        )
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
                    "ddtags": "env:,version:,openai.request.endpoint:/v1/images/edits,openai.request.method:POST,openai.request.model:dall-e,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>",  # noqa: E501
                    "dd.trace_id": "{:x}".format(trace_id),
                    "dd.span_id": str(span_id),
                    "prompt": "A sunlit indoor lounge area with a pool containing a flamingo",
                    "image": "image.png",
                    "mask": "mask.png",
                    "choices": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_image_variation(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_variation",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("image_variation.yaml"):
            openai.Image.create_variation(
                image=open(os.path.join(os.path.dirname(__file__), "test_data/image.png"), "rb"),
                api_key=request_api_key,
                n=1,
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_image_avariation(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_variation",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("image_variation_async.yaml"):
            await openai.Image.acreate_variation(
                image=open(os.path.join(os.path.dirname(__file__), "test_data/image.png"), "rb"),
                api_key=request_api_key,
                n=1,
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_logs_image_variation(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for image endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with openai_vcr.use_cassette("image_variation.yaml"):
        openai.Image.create_variation(
            image=open(os.path.join(os.path.dirname(__file__), "test_data/image.png"), "rb"),
            n=1,
            size="256x256",
            response_format="url",
            user="ddtrace-test",
        )

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
                    "ddtags": "env:,version:,openai.request.endpoint:/v1/images/variations,openai.request.method:POST,openai.request.model:dall-e,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>",  # noqa: E501
                    "dd.trace_id": "{:x}".format(trace_id),
                    "dd.span_id": str(span_id),
                    "image": "image.png",
                    "choices": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_image_edit_binary_input",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_image_edit_binary_input(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    image = Image.open(os.path.join(os.path.dirname(__file__), "test_data/image.png"))
    image_arr = BytesIO()
    image.save(image_arr, format="PNG")
    with openai_vcr.use_cassette("image_edit.yaml"):
        openai.Image.create_edit(
            image=image_arr.getvalue(),
            n=1,
            prompt="A sunlit indoor lounge area with a pool containing a flamingo",
            size="256x256",
            response_format="url",
            user="ddtrace-test",
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_image_b64_json_response",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_image_b64_json_response(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Image"):
        pytest.skip("image not supported for this version of openai")
    with openai_vcr.use_cassette("image_create_b64_json.yaml"):
        openai.Image.create(
            prompt="sleepy capybara with monkey on top",
            n=1,
            size="256x256",
            response_format="b64_json",
            user="ddtrace-test",
        )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_embedding(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_embedding", ignores=["meta.http.useragent", "meta.openai.base_url"]
    ):
        with openai_vcr.use_cassette("embedding.yaml"):
            openai.Embedding.create(
                api_key=request_api_key, input="hello world", model="text-embedding-ada-002", user="ddtrace-test"
            )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_embedding_string_array",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_embedding_string_array(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding_string_array.yaml"):
        openai.Embedding.create(
            input=["hello world", "hello again"], model="text-embedding-ada-002", user="ddtrace-test"
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_embedding_token_array",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_embedding_token_array(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding_token_array.yaml"):
        openai.Embedding.create(input=[1111, 2222, 3333], model="text-embedding-ada-002", user="ddtrace-test")


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_embedding_array_of_token_arrays",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_embedding_array_of_token_arrays(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding_array_of_token_arrays.yaml"):
        openai.Embedding.create(
            input=[[1111, 2222, 3333], [4444, 5555, 6666], [7777, 8888, 9999]],
            model="text-embedding-ada-002",
            user="ddtrace-test",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_aembedding(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_embedding",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("embedding_async.yaml"):
            await openai.Embedding.acreate(
                api_key=request_api_key, input="hello world", model="text-embedding-ada-002", user="ddtrace-test"
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_transcribe(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Audio"):
        pytest.skip("audio not supported for this version of openai")
    if api_key_in_env is False and parse_version(openai.version.VERSION) < (0, 27, 3):
        pytest.skip("API keys could not be supplied in the request before openai==0.27.3")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_transcribe",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("transcribe.yaml"):
            openai.Audio.transcribe(
                api_key=request_api_key,
                file=open(os.path.join(os.path.dirname(__file__), "test_data/english_audio.mp3"), "rb"),
                model="whisper-1",
                response_format="verbose_json",
                prompt="what's that over there?",
                temperature=0.3,
                language="en",
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_atranscribe(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Audio"):
        pytest.skip("audio not supported for this version of openai")
    if api_key_in_env is False and parse_version(openai.version.VERSION) < (0, 27, 3):
        pytest.skip("API keys could not be supplied in the request before openai==0.27.3")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_atranscribe",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("transcribe_async.yaml"):
            await openai.Audio.atranscribe(
                api_key=request_api_key,
                file=open(os.path.join(os.path.dirname(__file__), "test_data/english_audio.mp3"), "rb"),
                model="whisper-1",
                response_format="text",
                prompt="what's that over there?",
                temperature=0.7,
                language="en",
                user="ddtrace-test",
            )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_logs_transcribe(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for audio endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    if not hasattr(openai, "Audio"):
        pytest.skip("audio not supported for this version of openai")
    with openai_vcr.use_cassette("transcribe.yaml"):
        openai.Audio.transcribe(
            file=open(os.path.join(os.path.dirname(__file__), "test_data/english_audio.mp3"), "rb"),
            model="whisper-1",
            response_format="verbose_json",
            prompt="what's that over there?",
            temperature=0.3,
            language="en",
            user="ddtrace-test",
        )

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
                    "ddtags": "env:,version:,openai.request.endpoint:/v1/audio/transcriptions,openai.request.method:POST,openai.request.model:whisper-1,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>",  # noqa: E501
                    "dd.trace_id": "{:x}".format(trace_id),
                    "dd.span_id": str(span_id),
                    "file": "english_audio.mp3",
                    "prompt": "what's that over there?",
                    "language": "en",
                    "text": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_translate(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Audio"):
        pytest.skip("audio not supported for this version of openai")
    if api_key_in_env is False and parse_version(openai.version.VERSION) < (0, 27, 3):
        pytest.skip("API keys could not be supplied in the request before openai==0.27.3")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_translate",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("translate.yaml"):
            openai.Audio.translate(
                api_key=request_api_key,
                file=open(os.path.join(os.path.dirname(__file__), "test_data/french_audio.mp3"), "rb"),
                model="whisper-1",
                response_format="verbose_json",
                prompt="and when I've given up,",
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_atranslate(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Audio"):
        pytest.skip("audio not supported for this version of openai")
    if api_key_in_env is False and parse_version(openai.version.VERSION) < (0, 27, 3):
        pytest.skip("API keys could not be supplied in the request before openai==0.27.3")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_atranslate",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("translate_async.yaml"):
            await openai.Audio.atranslate(
                api_key=request_api_key,
                file=open(os.path.join(os.path.dirname(__file__), "test_data/french_audio.mp3"), "rb"),
                model="whisper-1",
                response_format="text",
                prompt="and when I've given up,",
                user="ddtrace-test",
            )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_logs_translate(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for audio endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    if not hasattr(openai, "Audio"):
        pytest.skip("audio not supported for this version of openai")
    with openai_vcr.use_cassette("translate.yaml"):
        openai.Audio.translate(
            file=open(os.path.join(os.path.dirname(__file__), "test_data/french_audio.mp3"), "rb"),
            model="whisper-1",
            response_format="verbose_json",
            prompt="and when I've given up,",
            user="ddtrace-test",
        )

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
                    "ddtags": "env:,version:,openai.request.endpoint:/v1/audio/translations,openai.request.method:POST,openai.request.model:whisper-1,openai.organization.name:datadog-4,openai.user.api_key:sk-...key>",  # noqa: E501
                    "dd.trace_id": "{:x}".format(trace_id),
                    "dd.span_id": str(span_id),
                    "file": "french_audio.mp3",
                    "prompt": "and when I've given up,",
                    "language": "",
                    "text": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_list(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_list", ignores=["meta.http.useragent", "meta.openai.base_url"]
    ):
        with openai_vcr.use_cassette("file_list.yaml"):
            openai.File.list(api_key=request_api_key, user="ddtrace-test")


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_alist(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_list", ignores=["meta.http.useragent", "meta.openai.base_url"]
    ):
        with openai_vcr.use_cassette("file_list_async.yaml"):
            await openai.File.alist(api_key=request_api_key, user="ddtrace-test")


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_create(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_create",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("file_create.yaml"):
            openai.File.create(
                api_key=request_api_key,
                file=open(os.path.join(os.path.dirname(__file__), "test_data/training_data.jsonl"), "rb"),
                purpose="fine-tune",
                user_provided_filename="dummy_training_file.jsonl",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_acreate(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_create",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("file_create_async.yaml"):
            await openai.File.acreate(
                api_key=request_api_key,
                file=open(os.path.join(os.path.dirname(__file__), "test_data/training_data.jsonl"), "rb"),
                purpose="fine-tune",
                user_provided_filename="dummy_training_file.jsonl",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_delete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_delete",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("file_delete.yaml"):
            openai.File.delete(
                sid="file-l48KgWVF75Tz2HLqLrcUdBPi",
                api_key=request_api_key,
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_adelete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_delete",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("file_delete_async.yaml"):
            await openai.File.adelete(
                sid="file-l48KgWVF75Tz2HLqLrcUdBPi",
                api_key=request_api_key,
                user="ddtrace-test",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_retrieve(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_retrieve",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("file_retrieve.yaml"):
            openai.File.retrieve(
                id="file-Aeh42OWPtbWgt7gfUjXBVFAF",
                api_key=request_api_key,
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_aretrieve(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_retrieve",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("file_retrieve_async.yaml"):
            await openai.File.aretrieve(
                id="file-Aeh42OWPtbWgt7gfUjXBVFAF",
                api_key=request_api_key,
                user="ddtrace-test",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_download(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_download",
        ignores=["meta.http.useragent", "meta.openai.base_url", "meta.openai.organization.name"],
    ):
        with openai_vcr.use_cassette("file_download.yaml"):
            openai.File.download(
                "file-xC22NUuYBkXvzRt2fLREcGde",
                api_key=request_api_key,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_adownload(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "File"):
        pytest.skip("file not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_download",
        ignores=[
            "meta.http.useragent",
            "metrics.openai.response.total_bytes",
            "meta.openai.base_url",
            "meta.openai.organization.name",
        ],
    ):
        # mock vcrpy stream response does not have `total_bytes` attribute
        with openai_vcr.use_cassette("file_download_async.yaml"):
            await openai.File.adownload(
                "file-xC22NUuYBkXvzRt2fLREcGde",
                api_key=request_api_key,
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_model_delete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Model"):
        pytest.skip("model not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_delete",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("model_delete.yaml"):
            openai.Model.delete(
                sid="babbage:ft-datadog:dummy-fine-tune-model-2023-06-01-23-15-52",
                api_key=request_api_key,
                user="ddtrace-test",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_model_adelete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Model"):
        pytest.skip("model not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_delete",
        ignores=["meta.http.useragent", "meta.openai.base_url"],
    ):
        with openai_vcr.use_cassette("model_delete_async.yaml"):
            await openai.Model.adelete(
                sid="babbage:ft-datadog:dummy-fine-tune-model-2023-06-01-23-15-52",
                api_key=request_api_key,
                user="ddtrace-test",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_create_moderation(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Moderation"):
        pytest.skip("moderation not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_create_moderation",
        ignores=["meta.http.useragent", "meta.openai.base_url", "meta.openai.response.id"],
    ):
        with openai_vcr.use_cassette("moderation.yaml"):
            openai.Moderation.create(
                api_key=request_api_key,
                input="i want to kill them.",
                model="text-moderation-latest",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_acreate_moderation(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "Moderation"):
        pytest.skip("moderation not supported for this version of openai")
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_create_moderation",
        ignores=["meta.http.useragent", "meta.openai.base_url", "meta.openai.response.id"],
    ):
        with openai_vcr.use_cassette("moderation_async.yaml"):
            await openai.Moderation.acreate(
                api_key=request_api_key,
                input="i want to kill them.",
                model="text-moderation-latest",
            )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_misuse",
    ignores=["meta.http.useragent", "meta.error.stack", "meta.openai.base_url"],
)
def test_misuse(openai, snapshot_tracer):
    with pytest.raises(openai.InvalidRequestError):
        openai.Completion.create(input="wrong arg")


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_span_finish_on_stream_error",
    ignores=["meta.http.useragent", "meta.error.stack", "meta.openai.base_url"],
)
def test_span_finish_on_stream_error(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("completion_stream_wrong_api_key.yaml"):
        with pytest.raises(openai.error.AuthenticationError):
            openai.Completion.create(
                api_key="sk-wrong-api-key",
                model="text-curie-001",
                prompt="how does openai tokenize prompts?",
                temperature=0.8,
                n=1,
                max_tokens=150,
                stream=True,
            )


def test_completion_stream(openai, openai_vcr, mock_metrics, mock_tracer):
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2]
            expected_completion = '! ... A page layouts page drawer? ... Interesting. The "Tools" is'
            resp = openai.Completion.create(model="ada", prompt="Hello world", stream=True)
            assert isinstance(resp, Generator)
            chunks = [c for c in resp]

    completion = "".join([c["choices"][0]["text"] for c in chunks])
    assert completion == expected_completion

    traces = mock_tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    assert traces[0][0].get_tag("openai.response.choices.0.text") == expected_completion
    assert traces[0][0].get_tag("openai.response.choices.0.finish_reason") == "length"

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.request.model:ada",
        "model:ada",
        "openai.request.endpoint:/v1/completions",
        "openai.request.method:POST",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
        "openai.estimated:true",
    ]
    if TIKTOKEN_AVAILABLE:
        prompt_expected_tags = expected_tags[:-1]
    else:
        prompt_expected_tags = expected_tags
    assert mock.call.distribution("tokens.prompt", 2, tags=prompt_expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", len(chunks), tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", len(chunks) + 2, tags=expected_tags) in mock_metrics.mock_calls


@pytest.mark.asyncio
async def test_completion_async_stream(openai, openai_vcr, mock_metrics, mock_tracer):
    with openai_vcr.use_cassette("completion_async_streamed.yaml"):
        with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2]
            expected_completion = "\" and just start creating stuff. Don't expect it to draw like this."
            resp = await openai.Completion.acreate(model="ada", prompt="Hello world", stream=True)
            assert isinstance(resp, AsyncGenerator)
            chunks = [c async for c in resp]

    completion = "".join([c["choices"][0]["text"] for c in chunks])
    assert completion == expected_completion

    traces = mock_tracer.pop_traces()
    assert len(traces) == 1
    assert traces[0][0].get_tag("openai.response.choices.0.text") == expected_completion
    assert traces[0][0].get_tag("openai.response.choices.0.finish_reason") == "length"

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.request.model:ada",
        "model:ada",
        "openai.request.endpoint:/v1/completions",
        "openai.request.method:POST",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
        "openai.estimated:true",
    ]
    if TIKTOKEN_AVAILABLE:
        expected_tags = expected_tags[:-1]
    assert mock.call.distribution("tokens.prompt", 2, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", 15, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", 17, tags=expected_tags) in mock_metrics.mock_calls


def test_chat_completion_stream(openai, openai_vcr, mock_metrics, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    with openai_vcr.use_cassette("chat_completion_streamed.yaml"):
        with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8]
            expected_completion = "The Los Angeles Dodgers won the World Series in 2020."
            resp = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "Who won the world series in 2020?"},
                ],
                stream=True,
                user="ddtrace-test",
            )
            prompt_tokens = 8
            span = snapshot_tracer.current_span()
            chunks = [c for c in resp]
            assert len(chunks) == 15
            completion = "".join([c["choices"][0]["delta"].get("content", "") for c in chunks])
            assert completion == expected_completion

    assert span.get_tag("openai.response.choices.0.message.content") == expected_completion
    assert span.get_tag("openai.response.choices.0.message.role") == "assistant"
    assert span.get_tag("openai.response.choices.0.finish_reason") == "stop"

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.request.model:gpt-3.5-turbo",
        "model:gpt-3.5-turbo",
        "openai.request.endpoint:/v1/chat/completions",
        "openai.request.method:POST",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    assert mock.call.distribution("request.duration", span.duration_ns, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.requests", 3, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.remaining.requests", 2, tags=expected_tags) in mock_metrics.mock_calls
    expected_tags += ["openai.estimated:true"]
    if TIKTOKEN_AVAILABLE:
        expected_tags = expected_tags[:-1]
    assert mock.call.distribution("tokens.prompt", prompt_tokens, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", 12, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", 12 + prompt_tokens, tags=expected_tags) in mock_metrics.mock_calls


@pytest.mark.asyncio
async def test_chat_completion_async_stream(openai, openai_vcr, mock_metrics, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with openai_vcr.use_cassette("chat_completion_streamed_async.yaml"):
        with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
            expected_completion = "As an AI language model, I do not have access to real-time information but as of the 2021 season, the captain of the Toronto Maple Leafs is John Tavares."  # noqa: E501
            resp = await openai.ChatCompletion.acreate(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "Who is the captain of the toronto maple leafs?"},
                ],
                stream=True,
                user="ddtrace-test",
            )
            prompt_tokens = 10
            span = snapshot_tracer.current_span()
            chunks = [c async for c in resp]
            assert len(chunks) == 39
            completion = "".join([c["choices"][0]["delta"].get("content", "") for c in chunks])
            assert completion == expected_completion

    assert span.get_tag("openai.response.choices.0.message.content") == expected_completion[:128] + "..."
    assert span.get_tag("openai.response.choices.0.message.role") == "assistant"
    assert span.get_tag("openai.response.choices.0.finish_reason") == "stop"

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "openai.request.model:gpt-3.5-turbo",
        "model:gpt-3.5-turbo",
        "openai.request.endpoint:/v1/chat/completions",
        "openai.request.method:POST",
        "openai.organization.id:",
        "openai.organization.name:datadog-4",
        "openai.user.api_key:sk-...key>",
        "error:0",
    ]
    assert mock.call.distribution("request.duration", span.duration_ns, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.requests", 3500, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.tokens", 90000, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.remaining.requests", 3499, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.gauge("ratelimit.remaining.tokens", 89971, tags=expected_tags) in mock_metrics.mock_calls
    expected_tags += ["openai.estimated:true"]
    if TIKTOKEN_AVAILABLE:
        expected_tags = expected_tags[:-1]
    assert mock.call.distribution("tokens.prompt", prompt_tokens, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.completion", 35, tags=expected_tags) in mock_metrics.mock_calls
    assert mock.call.distribution("tokens.total", 35 + prompt_tokens, tags=expected_tags) in mock_metrics.mock_calls


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_integration_sync", ignores=["meta.http.useragent"], async_mode=False
)
def test_integration_sync(openai_api_key, ddtrace_run_python_code_in_subprocess):
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
            "OPENAI_API_KEY": openai_api_key,
            "PYTHONPATH": ":".join(pypath),
            # Disable metrics because the test agent doesn't support metrics
            "DD_OPENAI_METRICS_ENABLED": "false",
        }
    )
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import openai
import ddtrace
from tests.contrib.openai.conftest import FilterOrg
from tests.contrib.openai.test_openai_v0 import get_openai_vcr
pin = ddtrace.Pin.get_from(openai)
pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})
with get_openai_vcr(subdirectory_name="v0").use_cassette("completion.yaml"):
    resp = openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_acompletion",
    ignores=[
        "meta.http.useragent",
        "meta.openai.base_url",
        "metrics.openai.response.usage.total_tokens",
        "metrics.openai.response.usage.prompt_tokens",
        "metrics.openai.response.usage.completion_tokens",
    ],
    async_mode=False,
)
def test_integration_async(openai_api_key, ddtrace_run_python_code_in_subprocess):
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
            "OPENAI_API_KEY": openai_api_key,
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
from tests.contrib.openai.conftest import FilterOrg
from tests.contrib.openai.test_openai_v0 import get_openai_vcr
pin = ddtrace.Pin.get_from(openai)
pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})
async def task():
    with get_openai_vcr(subdirectory_name="v0").use_cassette("completion_async.yaml"):
        resp = await openai.Completion.acreate(
            model="curie",
            prompt="As Descartes said, I think, therefore",
            temperature=0.8,
            n=1,
            max_tokens=150,
            user="ddtrace-test"
        )

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
            if span.get_tag("openai.response.choices.0.message.content"):
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
            if span.get_tag("openai.request.endpoint").endswith("/chat/completions"):
                prompt = span.get_tag("openai.request.messages.0.content")
                completion = span.get_tag("openai.response.choices.0.message.content")
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit
            else:
                prompt = span.get_tag("openai.request.prompt.0")
                completion = span.get_tag("openai.response.choices.0.text")
                # +3 for the ellipsis
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit


@pytest.mark.parametrize("ddtrace_config_openai", [dict(span_prompt_completion_sample_rate=0)])
def test_embedding_unsampled_prompt_completion(openai, openai_vcr, ddtrace_config_openai, mock_logs, mock_tracer):
    if not hasattr(openai, "Embedding"):
        pytest.skip("embedding not supported for this version of openai")
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(input="hello world", model="text-embedding-ada-002")
    logs = mock_logs.enqueue.call_count
    traces = mock_tracer.pop_traces()
    assert len(traces) == 1
    assert traces[0][0].get_tag("openai.request.input") is None
    assert logs == 0


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [dict(logs_enabled=True, log_prompt_completion_sample_rate=r) for r in [0, 0.25, 0.75, 1]],
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
    assert _est_tokens("") == 0  # oracle: 1
    assert _est_tokens("hello") == 1  # oracle: 1
    assert _est_tokens("hello, world") == 3  # oracle: 3
    assert _est_tokens("hello world") == 2  # oracle: 2
    assert _est_tokens("Hello world, how are you?") == 6  # oracle: 7
    assert _est_tokens("    hello    ") == 3  # oracle: 8
    assert (
        _est_tokens(
            "The GPT family of models process text using tokens, which are common sequences of characters found in text. The models understand the statistical relationships between these tokens, and excel at producing the next token in a sequence of tokens."  # noqa E501
        )
        == 54
    )  # oracle: 44
    assert (
        _est_tokens(
            "You can use the tool below to understand how a piece of text would be tokenized by the API, and the total count of tokens in that piece of text."  # noqa: E501
        )
        == 33
    )  # oracle: 33
    assert (
        _est_tokens(
            "A helpful rule of thumb is that one token generally corresponds to ~4 characters of text for common "
            "English text. This translates to roughly ¾ of a word (so 100 tokens ~= 75 words). If you need a "
            "programmatic interface for tokenizing text, check out our tiktoken package for Python. For JavaScript, "
            "the gpt-3-encoder package for node.js works for most GPT-3 models."
        )
        == 83
    )  # oracle: 87

    # Expected to be a disparity since our assumption is based on english words
    assert (
        _est_tokens(
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
        _est_tokens(
            "I want you to act as a linux terminal. I will type commands and you will reply with what the terminal should show. I want you to only reply with the terminal output inside one unique code block, and nothing else. do not write explanations. do not type commands unless I instruct you to do so. When I need to tell you something in English, I will do so by putting text inside curly brackets {like this}. My first command is pwd"  # noqa: E501
        )
        == 97
    )  # oracle: 92


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_completion",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_azure_openai_completion(openai_api_key, azure_openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_completion.yaml"):
        azure_openai.Completion.create(
            api_key=openai_api_key,
            engine="gpt-35-turbo",
            prompt="why do some languages have words that can't directly be translated to other languages?",
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_completion",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
async def test_azure_openai_acompletion(openai_api_key, azure_openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_completion_async.yaml"):
        await azure_openai.Completion.acreate(
            api_key=openai_api_key,
            engine="gpt-35-turbo",
            prompt="why do some languages have words that can't directly be translated to other languages?",
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_chat_completion",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_azure_openai_chat_completion(openai_api_key, azure_openai, openai_vcr, snapshot_tracer):
    if not hasattr(azure_openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with openai_vcr.use_cassette("azure_chat_completion.yaml"):
        azure_openai.ChatCompletion.create(
            api_key=openai_api_key,
            engine="gpt-35-turbo",
            messages=[{"role": "user", "content": "What's the weather like in NYC right now?"}],
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_chat_completion",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
async def test_azure_openai_chat_acompletion(openai_api_key, azure_openai, openai_vcr, snapshot_tracer):
    if not hasattr(azure_openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with openai_vcr.use_cassette("azure_chat_completion_async.yaml"):
        await azure_openai.ChatCompletion.acreate(
            api_key=openai_api_key,
            engine="gpt-35-turbo",
            messages=[{"role": "user", "content": "What's the weather like in NYC right now?"}],
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_embedding",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
def test_azure_openai_embedding(openai_api_key, azure_openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_embedding.yaml"):
        azure_openai.Embedding.create(
            api_key=openai_api_key,
            engine="text-embedding-ada-002",
            input="Hello world",
            temperature=0,
            user="ddtrace-test",
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_embedding",
    ignores=["meta.http.useragent", "meta.openai.base_url"],
)
async def test_azure_openai_aembedding(openai_api_key, azure_openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_embedding_async.yaml"):
        await azure_openai.Embedding.acreate(
            api_key=openai_api_key,
            engine="text-embedding-ada-002",
            input="Hello world",
            temperature=0,
            user="ddtrace-test",
        )


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
@pytest.mark.parametrize("service_name", [None, "mysvc"])
def test_integration_service_name(openai_api_key, ddtrace_run_python_code_in_subprocess, schema_version, service_name):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "OPENAI_API_KEY": openai_api_key,
            "PYTHONPATH": ":".join(pypath),
            # Disable metrics because the test agent doesn't support metrics
            "DD_OPENAI_METRICS_ENABLED": "false",
        }
    )
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    if service_name:
        env["DD_SERVICE"] = service_name
    with snapshot_context(
        token="tests.contrib.openai.test_openai_v0.test_integration_service_name[%s-%s]"
        % (service_name, schema_version),
        ignores=["meta.http.useragent", "meta.openai.base_url"],
        async_mode=False,
    ):
        out, err, status, pid = ddtrace_run_python_code_in_subprocess(
            """
import openai
import ddtrace
from tests.contrib.openai.conftest import FilterOrg
from tests.contrib.openai.test_openai_v0 import get_openai_vcr
pin = ddtrace.Pin.get_from(openai)
pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})
with get_openai_vcr(subdirectory_name="v0").use_cassette("completion.yaml"):
    resp = openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)
    """,
            env=env,
        )
        assert status == 0, err
        assert out == b""
        assert err == b""
