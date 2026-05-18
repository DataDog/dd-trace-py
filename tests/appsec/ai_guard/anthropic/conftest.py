"""Pytest fixtures and HTTP transport helpers for the AI Guard + Anthropic integration tests."""

import json

import httpx
import pytest

from ddtrace.appsec._ai_guard import init_ai_guard
from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active
from ddtrace.appsec._ai_guard._context import set_aiguard_context_active
from ddtrace.contrib.internal.anthropic.patch import patch
from ddtrace.contrib.internal.anthropic.patch import unpatch
from tests.appsec.ai_guard.utils import override_ai_guard_config
from tests.utils import override_env


@pytest.fixture
def aiguard_active_context():
    """Mark the current task as under active AI Guard evaluation for the test.

    Always resets on teardown — even if the test raises — so subsequent tests
    do not observe a leaked active counter.
    """
    token = set_aiguard_context_active()
    try:
        yield
    finally:
        reset_aiguard_context_active(token)


@pytest.fixture(scope="session", autouse=True)
def _ai_guard_session_init():
    """Initialise AI Guard once for the whole session and keep config active.

    Mirrors the OpenAI conftest: ``init_ai_guard()`` reads config eagerly, so
    holding the ``override_ai_guard_config`` context open via ``yield`` keeps
    session-scoped values (_dd_api_key, endpoint, ...) in place for every
    test.
    """
    with override_ai_guard_config(
        dict(
            _ai_guard_enabled="True",
            _ai_guard_endpoint="https://api.example.com/ai-guard",
            _dd_api_key="test-api-key",
            _dd_app_key="test-application-key",
        )
    ):
        init_ai_guard()
        yield


@pytest.fixture
def anthropic_sdk():
    # Force a dummy API key unconditionally: tests hit mock httpx transports,
    # never the real Anthropic API.
    with override_env(dict(ANTHROPIC_API_KEY="<not-a-real-key>")):
        patch()
        import anthropic

        try:
            yield anthropic
        finally:
            unpatch()


# ---------------------------------------------------------------------------
# Non-streaming mock transport
#
# Tests that exercise AI Guard before/after dispatch only need a canned
# Anthropic Messages response. Using a mock httpx transport avoids any cassette
# coupling and keeps these suites independent of the testagent.
# ---------------------------------------------------------------------------


def _fake_messages_body() -> bytes:
    return json.dumps(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-opus-20240229",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ).encode()


def _fake_messages_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=_fake_messages_body(),
    )


class _MessagesMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_messages_response()


class _AsyncMessagesMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_messages_response()


@pytest.fixture
def anthropic_client_mock(anthropic_sdk):
    return anthropic_sdk.Anthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_MessagesMockTransport()),
    )


@pytest.fixture
def async_anthropic_client_mock(anthropic_sdk):
    return anthropic_sdk.AsyncAnthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_AsyncMessagesMockTransport()),
    )


# ---------------------------------------------------------------------------
# Tool-use mock transport
#
# Returns a response with a ``tool_use`` content block so after-hook tests can
# assert that model-emitted tool calls are evaluated.
# ---------------------------------------------------------------------------


def _fake_tool_use_body() -> bytes:
    return json.dumps(
        {
            "id": "msg_tool_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-opus-20240229",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_test",
                    "name": "get_weather",
                    "input": {"location": "San Francisco, CA"},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }
    ).encode()


def _fake_tool_use_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=_fake_tool_use_body(),
    )


class _ToolUseMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_tool_use_response()


@pytest.fixture
def anthropic_client_tool_use(anthropic_sdk):
    return anthropic_sdk.Anthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_ToolUseMockTransport()),
    )


# ---------------------------------------------------------------------------
# Streaming fixtures — mock SSE transport
#
# Emits the minimal subset of Anthropic SSE events needed to drive the SDK's
# stream parser to completion: message_start, content_block_start,
# content_block_delta, content_block_stop, message_delta, message_stop.
# ---------------------------------------------------------------------------


def _sse_event(event_name: str, data: dict) -> bytes:
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n".encode()


def _fake_stream_chunks() -> bytes:
    return b"".join(
        [
            _sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_stream_test",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-3-opus-20240229",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 0},
                    },
                },
            ),
            _sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "ok"},
                },
            ),
            _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ),
            _sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            _sse_event("message_stop", {"type": "message_stop"}),
        ]
    )


def _fake_stream_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        stream=httpx.ByteStream(_fake_stream_chunks()),
    )


class _StreamMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_stream_response()


class _AsyncStreamMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_stream_response()


@pytest.fixture
def anthropic_client_stream(anthropic_sdk):
    return anthropic_sdk.Anthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_StreamMockTransport()),
    )


@pytest.fixture
def async_anthropic_client_stream(anthropic_sdk):
    return anthropic_sdk.AsyncAnthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_AsyncStreamMockTransport()),
    )
