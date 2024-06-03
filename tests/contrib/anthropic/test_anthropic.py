import pytest

from tests.contrib.anthropic.utils import get_request_vcr
from tests.utils import override_global_config


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


def test_global_tags(ddtrace_config_anthropic, anthropic, request_vcr, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = anthropic.Anthropic()
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        cassette_name = "anthropic_completion_sync_39.yaml"
        with request_vcr.use_cassette(cassette_name):
            llm.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=1024,
                messages=[{"role": "user", "content": "What does Nietzsche mean by 'God is dead'?"}],
            )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "Messages.create"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("anthropic.request.model") == "claude-3-opus-20240229"
    assert span.get_tag("anthropic.request.api_key") == "...key>"


# @pytest.mark.snapshot(ignores=["metrics.anthropic.tokens.total_cost", "resource"])
@pytest.mark.snapshot()
def test_anthropic_llm_sync(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_sync.yaml"):
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=1024,
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


@pytest.mark.snapshot()
def test_anthropic_llm_sync_multiple_prompts(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_sync_multi_prompt.yaml"):
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello, I am looking for information about some books!"},
                        {"type": "text", "text": "Can you explain what Descartes meant by 'I think, therefore I am'?"},
                    ],
                }
            ],
        )


@pytest.mark.snapshot()
def test_anthropic_llm_sync_multiple_prompts_with_chat_history(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_sync_multi_prompt_with_chat_history.yaml"):
        llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=1024,
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


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_anthropic_llm_error(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    invalid_error = anthropic.BadRequestError
    with pytest.raises(invalid_error):
        with request_vcr.use_cassette("anthropic_completion_error.yaml"):
            llm.messages.create(model="claude-3-opus-20240229", max_tokens=1024, messages=["Invalid content"])


@pytest.mark.snapshot()
def test_anthropic_llm_sync_stream(anthropic, request_vcr):
    llm = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion_sync_stream.yaml"):
        stream = llm.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=1024,
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
        for chunk in stream:
            print(chunk.type)

