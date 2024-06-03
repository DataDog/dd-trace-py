import pytest

from tests.utils import override_global_config


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
        cassette_name = "anthropic_completion_async.yaml"
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
# @pytest.mark.snapshot
async def test_anthropic_llm_async_basic(anthropic, request_vcr, snapshot_context):
    with snapshot_context():
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_async.yaml"):
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
async def test_anthropic_llm_async_multiple_prompts_no_history(anthropic, request_vcr, snapshot_context):
    with snapshot_context():
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_async_multi_prompt.yaml"):
            await llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=15,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Hello, I am looking for information about some books!"},
                            {
                                "type": "text",
                                "text": "Can you explain what Descartes meant by 'I think, therefore I am'?",
                            },
                        ],
                    }
                ],
            )


@pytest.mark.asyncio
async def test_anthropic_llm_async_multiple_prompts_with_chat_history(anthropic, request_vcr, snapshot_context):
    with snapshot_context():
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_async_multi_prompt_with_chat_history.yaml"):
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
    with snapshot_context():
        llm = anthropic.AsyncAnthropic()
        invalid_error = anthropic.BadRequestError
        with pytest.raises(invalid_error):
            with request_vcr.use_cassette("anthropic_completion_error_async.yaml"):
                await llm.messages.create(model="claude-3-opus-20240229", max_tokens=15, messages=["Invalid content"])


@pytest.mark.asyncio
async def test_anthropic_llm_async_stream(anthropic, request_vcr, snapshot_context):
    with snapshot_context(ignores=["meta.error.stack"]):
        llm = anthropic.AsyncAnthropic()
        with request_vcr.use_cassette("anthropic_completion_async_stream.yaml"):
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
