import json

import httpx
import pytest

from ddtrace.appsec._ai_guard import init_ai_guard
from ddtrace.contrib.internal.openai.patch import patch
from ddtrace.contrib.internal.openai.patch import unpatch
from tests.appsec.ai_guard.utils import override_ai_guard_config
from tests.utils import override_env


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    with override_ai_guard_config(
        dict(
            _ai_guard_enabled="True",
            _ai_guard_endpoint="https://api.example.com/ai-guard",
            _dd_api_key="test-api-key",
            _dd_app_key="test-application-key",
        )
    ):
        init_ai_guard()


@pytest.fixture
def openai_sdk():
    # Force a dummy API key unconditionally: tests hit the testagent VCR / mock
    # transport, never the real OpenAI API. Falling back to the developer's
    # real OPENAI_API_KEY would risk leaking it into recorded cassettes if a
    # test regenerates them.
    with override_env(dict(OPENAI_API_KEY="<not-a-real-key>")):
        patch()
        import openai

        yield openai
        unpatch()


@pytest.fixture
def openai_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture requests to OpenAI.

    The request body determines which cassette is replayed (see
    ``ddapm_test_agent.vcr_proxy._generate_cassette_name``). In CI the
    ``ai_guard_openai`` suitespec entry sets the testagent's
    ``VCR_CASSETTES_DIRECTORY`` to ``${CI_PROJECT_DIR}/tests/appsec/_cassettes``
    so cassettes under ``tests/appsec/_cassettes/openai/`` are used. Locally
    the shared ``docker-compose`` testagent still mounts
    ``tests/llmobs/llmobs_cassettes`` so a duplicate of each ai_guard cassette
    is kept in both locations — update both when regenerating.
    """
    return "http://localhost:9126/vcr/openai"


@pytest.fixture
def openai_client(openai_sdk, openai_url):
    return openai_sdk.OpenAI(api_key="<not-a-real-key>", base_url=openai_url)


@pytest.fixture
def async_openai_client(openai_sdk, openai_url):
    return openai_sdk.AsyncOpenAI(api_key="<not-a-real-key>", base_url=openai_url)


# ---------------------------------------------------------------------------
# Streaming fixtures — mock httpx transport
#
# The ddtrace openai patch injects ``stream_options={"include_usage": True}``
# into the request kwargs for streaming chat completions (see
# ``_endpoint_hooks._ChatCompletionHook._record_request``). No 200-response
# cassette in the testagent VCR carries this field, so streaming tests use a
# lightweight httpx transport instead to validate the AI Guard dispatch path.
# ---------------------------------------------------------------------------


def _fake_stream_chunks() -> bytes:
    chunks = [
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {"role": "assistant"}}]},
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {"content": "Hi"}}]},
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    lines = [b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks]
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


def _fake_stream_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        stream=httpx.ByteStream(_fake_stream_chunks()),
    )


class _StreamMockTransport(httpx.BaseTransport):
    def handle_request(self, request):
        return _fake_stream_response()


class _AsyncStreamMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        return _fake_stream_response()


@pytest.fixture
def openai_client_stream(openai_sdk):
    return openai_sdk.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_StreamMockTransport()),
    )


@pytest.fixture
def async_openai_client_stream(openai_sdk):
    return openai_sdk.AsyncOpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_AsyncStreamMockTransport()),
    )


# ---------------------------------------------------------------------------
# Non-streaming mock transport
#
# The testagent VCR only matches specific request payloads (byte-hashed body).
# Tests that send arbitrary message shapes (e.g. system-only, assistant+tool)
# don't match any cassette and would otherwise hit APIConnectionError with
# retries. This transport returns a canned chat.completion response so those
# tests can exercise the before/after AI Guard dispatch path without cassette
# coupling.
# ---------------------------------------------------------------------------


def _fake_chat_completion_body() -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-3.5-turbo",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()


def _fake_chat_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=_fake_chat_completion_body(),
    )


class _ChatMockTransport(httpx.BaseTransport):
    def handle_request(self, request):
        return _fake_chat_response()


class _AsyncChatMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        return _fake_chat_response()


@pytest.fixture
def openai_client_mock(openai_sdk):
    return openai_sdk.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_ChatMockTransport()),
    )


@pytest.fixture
def async_openai_client_mock(openai_sdk):
    return openai_sdk.AsyncOpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_AsyncChatMockTransport()),
    )
