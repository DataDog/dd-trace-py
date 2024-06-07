import anthropic as anthropic_module
import pytest

from ddtrace.internal.utils.version import parse_version
from tests.utils import override_global_config

from .utils import tools


ANTHROPIC_VERSION = parse_version(anthropic_module.__version__)


def test_global_tags(ddtrace_config_anthropic, anthropic, request_vcr, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = anthropic.Anthropic()
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        cassette_name = "anthropic_completion.yaml"
        with request_vcr.use_cassette(cassette_name):
            llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                messages=[{"role": "user", "content": "What does Nietzsche mean by 'God is dead'?"}],
            )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "Messages.create"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("anthropic.request.model") == "claude-3-opus-20240229"
    assert span.get_tag("anthropic.request.api_key") == "sk-...key>"


@pytest.mark.snapshot(token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm", ignores=["resource"])
def test_anthropic_llm_sync(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion.yaml"):
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=15,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                        }
                    ],
                }
            ],
        )


@pytest.mark.snapshot(
    token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_multiple_prompts", ignores=["resource"]
)
def test_anthropic_llm_sync_multiple_prompts(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_multi_prompt.yaml"):
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=15,
            system="Respond only in all caps.",
            temperature=0.8,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello, I am looking for information about some books!"},
                        {"type": "text", "text": "What is the best selling book?"},
                    ],
                }
            ],
        )


@pytest.mark.snapshot(
    token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_multiple_prompts_with_chat_history",
    ignores=["resource"],
)
def test_anthropic_llm_sync_multiple_prompts_with_chat_history(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_multi_prompt_with_chat_history.yaml"):
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=30,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello, Start all responses with your name Claude."},
                        {"type": "text", "text": "End all responses with [COPY, CLAUDE OVER AND OUT!]"},
                    ],
                },
                {"role": "assistant", "content": "Claude: Sure! [COPY, CLAUDE OVER AND OUT!]"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Add the time and date to the beginning of your response after your name.",
                        },
                        {"type": "text", "text": "Explain string theory succinctly to a complete noob."},
                    ],
                },
            ],
        )


@pytest.mark.snapshot(
    ignores=["meta.error.stack", "resource"], token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_error"
)
def test_anthropic_llm_error(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    invalid_error = anthropic.BadRequestError
    with pytest.raises(invalid_error):
        with request_vcr.use_cassette("anthropic_completion_error.yaml"):
            llm.messages.create(model="claude-3-opus-20240229", max_tokens=15, messages=["Invalid content"])


@pytest.mark.snapshot(token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_stream", ignores=["resource"])
def test_anthropic_llm_sync_stream(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_stream.yaml"):
        stream = llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=15,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                        }
                    ],
                },
            ],
            stream=True,
        )
        for _ in stream:
            pass


@pytest.mark.snapshot(token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_tools", ignores=["resource"])
@pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
def test_anthropic_llm_sync_tools(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_tools.yaml"):
        message = llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=200,
            messages=[{"role": "user", "content": "What is the result of 1,984,135 * 9,343,116?"}],
            tools=tools,
        )
        assert message is not None


# Async tests


@pytest.mark.asyncio
async def test_global_tags_async(ddtrace_config_anthropic, anthropic, request_vcr, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = anthropic.AsyncAnthropic()
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        cassette_name = "anthropic_completion.yaml"
        with request_vcr.use_cassette(cassette_name):
            await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                messages=[{"role": "user", "content": "What does Nietzsche mean by 'God is dead'?"}],
            )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "AsyncMessages.create"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("anthropic.request.model") == "claude-3-opus-20240229"
    assert span.get_tag("anthropic.request.api_key") == "sk-...key>"


@pytest.mark.asyncio
async def test_anthropic_llm_async_basic(anthropic, request_vcr, snapshot_context):
    with snapshot_context(
        token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_basic", ignores=["resource"]
    ):
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion.yaml"):
            await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                            }
                        ],
                    }
                ],
            )


@pytest.mark.asyncio
async def test_anthropic_llm_async_multiple_prompts(anthropic, request_vcr, snapshot_context):
    with snapshot_context(
        token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_multiple_prompts",
        ignores=["resource"],
    ):
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_multi_prompt.yaml"):
            await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                system="Respond only in all caps.",
                temperature=0.8,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Hello, I am looking for information about some books!"},
                            {
                                "type": "text",
                                "text": "What is the best selling book?",
                            },
                        ],
                    }
                ],
            )


@pytest.mark.asyncio
async def test_anthropic_llm_async_multiple_prompts_with_chat_history(anthropic, request_vcr, snapshot_context):
    with snapshot_context(
        token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_multiple_prompts_with_chat_history",
        ignores=["resource"],
    ):
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_multi_prompt_with_chat_history.yaml"):
            await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=30,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Hello, Start all responses with your name Claude."},
                            {"type": "text", "text": "End all responses with [COPY, CLAUDE OVER AND OUT!]"},
                        ],
                    },
                    {"role": "assistant", "content": "Claude: Sure! [COPY, CLAUDE OVER AND OUT!]"},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Add the time and date to the beginning of your response after your name.",
                            },
                            {"type": "text", "text": "Explain string theory succinctly to a complete noob."},
                        ],
                    },
                ],
            )


@pytest.mark.asyncio
async def test_anthropic_llm_error_async(anthropic, request_vcr, snapshot_context):
    with snapshot_context(
        ignores=["meta.error.stack", "resource"],
        token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_error",
    ):
        llm = anthropic.AsyncAnthropic()
        invalid_error = anthropic.BadRequestError
        with pytest.raises(invalid_error):
            with request_vcr.use_cassette("anthropic_completion_error.yaml"):
                await llm.messages.create(model="claude-3-opus-20240229", max_tokens=15, messages=["Invalid content"])


@pytest.mark.asyncio
async def test_anthropic_llm_async_stream(anthropic, request_vcr, snapshot_context):
    with snapshot_context(
        token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_stream", ignores=["resource"]
    ):
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_stream.yaml"):
            stream = await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                            }
                        ],
                    },
                ],
                stream=True,
            )
            async for _ in stream:
                pass


@pytest.mark.skipif(ANTHROPIC_VERSION < (0, 27), reason="Anthropic Tools not available until 0.27.0, skipping.")
async def test_anthropic_llm_async_tools(anthropic, request_vcr, snapshot_context):
    with snapshot_context(
        token="tests.contrib.anthropic.test_anthropic.test_anthropic_llm_tools", ignores=["resource"]
    ):
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_tools.yaml"):
            message = await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=200,
                messages=[{"role": "user", "content": "What is the result of 1,984,135 * 9,343,116?"}],
                tools=tools,
            )
            assert message is not None
