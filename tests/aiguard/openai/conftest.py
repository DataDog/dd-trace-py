"""Pytest fixtures and HTTP transport helpers for the AI Guard + OpenAI integration tests."""

import json

import httpx
import pytest

from ddtrace.aiguard._context import reset_aiguard_context_active
from ddtrace.aiguard._context import set_aiguard_context_active
from ddtrace.aiguard._initialization import load_ai_guard
from ddtrace.contrib.internal.openai.patch import patch
from ddtrace.contrib.internal.openai.patch import unpatch
from tests.aiguard.utils import override_ai_guard_config
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

    ``load_ai_guard()`` reads config eagerly; holding the ``override_ai_guard_config``
    context open via ``yield`` keeps session-scoped values (_dd_api_key, endpoint,
    …) in place for every test rather than tearing them down the instant the init
    call returns.
    """
    with override_ai_guard_config(
        dict(
            _ai_guard_enabled="True",
            _ai_guard_endpoint="https://api.example.com/ai-guard",
            _dd_api_key="test-api-key",
            _dd_app_key="test-application-key",
        )
    ):
        load_ai_guard()
        yield


@pytest.fixture
def _require_responses_api():
    """Skip when the installed OpenAI SDK lacks the Responses API (added in ~1.66).

    Older SDKs (e.g. the 1.3.0 pin exercising the plain-generator streaming path) have no
    ``responses`` resource, so any fixture building a Responses client requests this first.
    """
    import openai

    if not hasattr(getattr(openai, "resources", None), "responses"):
        pytest.skip("Responses API requires openai>=1.66")


@pytest.fixture
def openai_sdk():
    # Force a dummy API key unconditionally: tests hit the testagent VCR / mock
    # transport, never the real OpenAI API. Falling back to the developer's
    # real OPENAI_API_KEY would risk leaking it into recorded cassettes if a
    # test regenerates them.
    with override_env(dict(OPENAI_API_KEY="<not-a-real-key>")):
        patch()
        import openai

        try:
            yield openai
        finally:
            unpatch()


@pytest.fixture
def openai_sdk_buffered():
    """Like ``openai_sdk`` but with stream-response evaluation enabled.

    Enables ``DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED`` *before* calling
    ``patch()`` so ``_install_openai_wrappers`` sees the flag as True and
    actually installs the ``BufferedAIGuardStream`` wrappers.
    """
    with override_env(dict(OPENAI_API_KEY="<not-a-real-key>")):
        with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=True)):
            patch()
            import openai

            try:
                yield openai
            finally:
                unpatch()


@pytest.fixture
def openai_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture requests to OpenAI.

    The request body determines which cassette is replayed (see
    ``ddapm_test_agent.vcr_proxy._generate_cassette_name``). Cassettes live
    under the shared top-level ``tests/cassettes/openai/`` directory
    (consolidated via PR #17715) — the same directory the
    ``ai_guard_langchain``, ``contrib/langchain``, and ``contrib/openai``
    suites read from, so recordings of identical request bodies are reused
    across suites without duplication.
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
    # ``index`` is required on each choice: openai <1.6 has no default for it, so the
    # contrib's ``_loop_handler`` (``streamed_chunks[choice.index]``) raises on None.
    chunks = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant"}}],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "Hi"}}],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    lines = [b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks]
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


def _fake_tool_call_stream_chunks() -> bytes:
    """Stream a tool_call across deltas (name first, arguments second) — the shape that must
    survive reconstruction so the buffered response evaluation actually sees the tool call.
    """
    chunks = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": ""},
                            }
                        ],
                    },
                }
            ],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"city": "Paris"}'}}]}}
            ],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        },
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
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_stream_response()


class _AsyncStreamMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_stream_response()


def _fake_tool_call_stream_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        stream=httpx.ByteStream(_fake_tool_call_stream_chunks()),
    )


class _ToolCallStreamMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_tool_call_stream_response()


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


@pytest.fixture
def openai_client_stream_buffered(openai_sdk_buffered):
    return openai_sdk_buffered.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_StreamMockTransport()),
    )


@pytest.fixture
def async_openai_client_stream_buffered(openai_sdk_buffered):
    return openai_sdk_buffered.AsyncOpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_AsyncStreamMockTransport()),
    )


@pytest.fixture
def openai_client_stream_tool_calls_buffered(openai_sdk_buffered):
    return openai_sdk_buffered.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_ToolCallStreamMockTransport()),
    )


# ---------------------------------------------------------------------------
# Responses API streaming — mock httpx transport (SSE)
#
# The Responses API streams Server-Sent Events typed by a ``type`` field; the
# terminal ``response.completed`` event carries the full ``response`` snapshot
# with the generated ``output``. Mirrors the wire format from the contrib
# cassette ``tests/contrib/openai/cassettes/v1/response_stream.yaml``.
# ---------------------------------------------------------------------------


def _fake_response_snapshot() -> dict:
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 0,
        "model": "gpt-4o-mini",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "output": [
            {
                "id": "msg-test",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok", "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "usage": None,
        "user": None,
        "metadata": {},
    }


def _sse(event: str, data: dict) -> bytes:
    return ("event: " + event + "\ndata: " + json.dumps(data) + "\n\n").encode()


def _fake_responses_stream_chunks() -> bytes:
    msg_id = "msg-test"
    events = [
        _sse(
            "response.output_text.delta",
            {
                "type": "response.output_text.delta",
                "item_id": msg_id,
                "output_index": 0,
                "content_index": 0,
                "delta": "ok",
            },
        ),
        _sse(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "id": msg_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                },
            },
        ),
        _sse("response.completed", {"type": "response.completed", "response": _fake_response_snapshot()}),
    ]
    return b"".join(events)


def _fake_responses_stream_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        stream=httpx.ByteStream(_fake_responses_stream_chunks()),
    )


class _ResponsesStreamMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_responses_stream_response()


class _AsyncResponsesStreamMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_responses_stream_response()


@pytest.fixture
def openai_responses_stream_client(openai_sdk, _require_responses_api):
    return openai_sdk.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_ResponsesStreamMockTransport()),
    )


@pytest.fixture
def openai_responses_stream_client_buffered(openai_sdk_buffered, _require_responses_api):
    return openai_sdk_buffered.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_ResponsesStreamMockTransport()),
    )


@pytest.fixture
def async_openai_responses_stream_client_buffered(openai_sdk_buffered, _require_responses_api):
    return openai_sdk_buffered.AsyncOpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_AsyncResponsesStreamMockTransport()),
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
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_chat_response()


class _AsyncChatMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
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


# ---------------------------------------------------------------------------
# Responses API mock transport
#
# The Responses API uses a different request/response shape than Chat
# Completions (``input`` instead of ``messages``, ``output`` list of typed
# items instead of ``choices``). Tests exercise the AI Guard dispatch path
# without requiring a real OpenAI Responses cassette or a live API call.
# ---------------------------------------------------------------------------


def _fake_response_body() -> bytes:
    # Full payload shape (metadata, parallel_tool_calls, tool_choice, …) — the
    # OpenAI SDK validates response payloads against pydantic models that
    # tighten across releases, so we mirror the real wire format rather than a
    # minimal stub.
    return json.dumps(
        {
            "id": "resp-test",
            "object": "response",
            "created_at": 0,
            "model": "gpt-4o-mini",
            "status": "completed",
            "output": [
                {
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
            "metadata": {},
            "parallel_tool_calls": True,
            "previous_response_id": None,
            "temperature": 1.0,
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1.0,
        }
    ).encode()


def _fake_response_http() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=_fake_response_body(),
    )


class _ResponseMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_response_http()


class _AsyncResponseMockTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _fake_response_http()


@pytest.fixture
def openai_responses_client_mock(openai_sdk, _require_responses_api):
    return openai_sdk.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_ResponseMockTransport()),
    )


@pytest.fixture
def async_openai_responses_client_mock(openai_sdk, _require_responses_api):
    return openai_sdk.AsyncOpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_AsyncResponseMockTransport()),
    )
