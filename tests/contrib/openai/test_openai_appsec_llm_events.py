"""Unit and integration tests for the AppSec OpenAI LLM business logic events handler.

Tests verify that:
- OpenAI chat/completions/responses calls dispatch the correct core events.
- The AppSec handler calls call_waf_callback with the expected LLM event data.
- No WAF call is made when there is no active ASM context.
- Azure OpenAI (engine kwarg) is handled correctly.
- WAF exceptions do not propagate to the caller.
- Streaming calls still emit the before event.
- Async paths dispatch correctly.
- Only the first LLM call per request reaches the WAF (PERSISTENT_ADDRESSES dedup).
- A real ASM context + WAF rule sets the expected span tags.
"""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from ddtrace.internal import core


# ---------------------------------------------------------------------------
# Unit tests for the handler function
# ---------------------------------------------------------------------------


class TestOnOpenAILlmCall:
    def test_calls_waf_callback_with_provider_and_model(self):
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
            patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
        ):
            _on_openai_llm_call({"model": "gpt-4.1", "messages": [{"role": "user", "content": "hi"}]})

        mock_waf.assert_called_once_with({"LLM_EVENT": {"provider": "openai", "model": "gpt-4.1"}})

    def test_no_waf_call_when_no_asm_context(self):
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=False),
            patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
        ):
            _on_openai_llm_call({"model": "gpt-4.1"})

        mock_waf.assert_not_called()

    def test_missing_model_passed_as_empty_string(self):
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
            patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
        ):
            _on_openai_llm_call({})

        mock_waf.assert_called_once_with({"LLM_EVENT": {"provider": "openai", "model": ""}})

    def test_none_model_passed_as_empty_string(self):
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
            patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
        ):
            _on_openai_llm_call({"model": None})

        mock_waf.assert_called_once_with({"LLM_EVENT": {"provider": "openai", "model": ""}})

    def test_engine_kwarg_used_for_azure(self):
        """Azure OpenAI passes 'engine' instead of 'model'."""
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
            patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
        ):
            _on_openai_llm_call({"engine": "my-gpt4-deployment"})

        mock_waf.assert_called_once_with({"LLM_EVENT": {"provider": "openai", "model": "my-gpt4-deployment"}})

    def test_model_takes_precedence_over_engine(self):
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
            patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
        ):
            _on_openai_llm_call({"model": "gpt-4.1", "engine": "other"})

        mock_waf.assert_called_once_with({"LLM_EVENT": {"provider": "openai", "model": "gpt-4.1"}})

    def test_waf_exception_does_not_propagate(self):
        """Exceptions from call_waf_callback must not bubble up to the OpenAI caller."""
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
            patch(
                "ddtrace.appsec._contrib.openai.handlers.call_waf_callback",
                side_effect=RuntimeError("waf exploded"),
            ),
        ):
            # Must not raise
            _on_openai_llm_call({"model": "gpt-4.1"})


# ---------------------------------------------------------------------------
# Mock HTTP transports
# ---------------------------------------------------------------------------


def _fake_chat_completion_response() -> httpx.Response:
    body = json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4.1",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()
    return httpx.Response(200, headers={"content-type": "application/json"}, content=body)


def _fake_completion_response() -> httpx.Response:
    body = json.dumps(
        {
            "id": "cmpl-test",
            "object": "text_completion",
            "created": 0,
            "model": "gpt-3.5-turbo-instruct",
            "choices": [{"text": "ok", "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()
    return httpx.Response(200, headers={"content-type": "application/json"}, content=body)


def _fake_responses_response() -> httpx.Response:
    body = json.dumps(
        {
            "id": "resp-test",
            "object": "response",
            "created_at": 0,
            "model": "gpt-4.1",
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
    return httpx.Response(200, headers={"content-type": "application/json"}, content=body)


def _fake_stream_chunks() -> bytes:
    chunks = [
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {"role": "assistant"}}]},
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {"content": "hi"}}]},
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {}, "finish_reason": "stop"}],
        },
    ]
    lines = [b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks]
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


class _SyncMockTransport(httpx.BaseTransport):
    def __init__(self, response_factory):
        self._factory = response_factory

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._factory()


class _AsyncMockTransport(httpx.AsyncBaseTransport):
    def __init__(self, response_factory):
        self._factory = response_factory

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._factory()


@pytest.fixture
def openai_sdk():
    from ddtrace.contrib.internal.openai.patch import patch as openai_patch
    from ddtrace.contrib.internal.openai.patch import unpatch as openai_unpatch
    from tests.utils import override_env

    with override_env({"OPENAI_API_KEY": "<test-key>"}):
        openai_patch()
        import openai

        try:
            yield openai
        finally:
            openai_unpatch()


# ---------------------------------------------------------------------------
# Tests for core event dispatch (sync)
# ---------------------------------------------------------------------------


class TestCoreEventDispatch:
    """Test that the OpenAI patch dispatches the correct core events."""

    def test_chat_completions_dispatches_before_event(self, openai_sdk):
        received = []
        core.on("openai.chat.completions.create.before", received.append)
        try:
            client = openai_sdk.OpenAI(
                api_key="<test-key>",
                http_client=httpx.Client(transport=_SyncMockTransport(_fake_chat_completion_response)),
            )
            client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": "hi"}])
        finally:
            core.reset_listeners("openai.chat.completions.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-4.1"

    def test_completions_dispatches_before_event(self, openai_sdk):
        received = []
        core.on("openai.completions.create.before", received.append)
        try:
            client = openai_sdk.OpenAI(
                api_key="<test-key>",
                http_client=httpx.Client(transport=_SyncMockTransport(_fake_completion_response)),
            )
            client.completions.create(model="gpt-3.5-turbo-instruct", prompt="say hi")
        finally:
            core.reset_listeners("openai.completions.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-3.5-turbo-instruct"

    def test_responses_dispatches_before_event(self, openai_sdk):
        try:
            import openai.resources.responses  # noqa: F401
        except ImportError:
            pytest.skip("openai.resources.responses not available")

        received = []
        core.on("openai.responses.create.before", received.append)
        try:
            client = openai_sdk.OpenAI(
                api_key="<test-key>",
                http_client=httpx.Client(transport=_SyncMockTransport(_fake_responses_response)),
            )
            client.responses.create(model="gpt-4.1", input="hi")
        finally:
            core.reset_listeners("openai.responses.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-4.1"

    def test_streaming_chat_completions_dispatches_before_event(self, openai_sdk):
        """stream=True still fires the .before event."""
        received = []
        core.on("openai.chat.completions.create.before", received.append)
        try:
            client = openai_sdk.OpenAI(
                api_key="<test-key>",
                http_client=httpx.Client(
                    transport=_SyncMockTransport(
                        lambda: httpx.Response(
                            200,
                            headers={"content-type": "text/event-stream"},
                            stream=httpx.ByteStream(_fake_stream_chunks()),
                        )
                    )
                ),
            )
            stream = client.chat.completions.create(
                model="gpt-4.1", messages=[{"role": "user", "content": "hi"}], stream=True
            )
            for _ in stream:
                pass
        finally:
            core.reset_listeners("openai.chat.completions.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-4.1"


# ---------------------------------------------------------------------------
# Tests for core event dispatch (async)
# ---------------------------------------------------------------------------


class TestAsyncCoreEventDispatch:
    """Test that async OpenAI methods also dispatch .before events."""

    def test_async_chat_completions_dispatches_before_event(self, openai_sdk):
        received = []
        core.on("openai.chat.completions.create.before", received.append)
        try:

            async def run():
                client = openai_sdk.AsyncOpenAI(
                    api_key="<test-key>",
                    http_client=httpx.AsyncClient(transport=_AsyncMockTransport(_fake_chat_completion_response)),
                )
                await client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": "hi"}])

            asyncio.run(run())
        finally:
            core.reset_listeners("openai.chat.completions.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-4.1"

    def test_async_completions_dispatches_before_event(self, openai_sdk):
        received = []
        core.on("openai.completions.create.before", received.append)
        try:

            async def run():
                client = openai_sdk.AsyncOpenAI(
                    api_key="<test-key>",
                    http_client=httpx.AsyncClient(transport=_AsyncMockTransport(_fake_completion_response)),
                )
                await client.completions.create(model="gpt-3.5-turbo-instruct", prompt="hi")

            asyncio.run(run())
        finally:
            core.reset_listeners("openai.completions.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-3.5-turbo-instruct"

    def test_async_responses_dispatches_before_event(self, openai_sdk):
        try:
            import openai.resources.responses  # noqa: F401
        except ImportError:
            pytest.skip("openai.resources.responses not available")

        received = []
        core.on("openai.responses.create.before", received.append)
        try:

            async def run():
                client = openai_sdk.AsyncOpenAI(
                    api_key="<test-key>",
                    http_client=httpx.AsyncClient(transport=_AsyncMockTransport(_fake_responses_response)),
                )
                await client.responses.create(model="gpt-4.1", input="hi")

            asyncio.run(run())
        finally:
            core.reset_listeners("openai.responses.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-4.1"


# ---------------------------------------------------------------------------
# Integration: handler + WAF callback chain
# ---------------------------------------------------------------------------


class TestHandlerIntegration:
    """Test _on_openai_llm_call is triggered by the .before event, calling call_waf_callback."""

    def test_chat_completions_calls_waf_callback(self, openai_sdk):
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        core.on("openai.chat.completions.create.before", _on_openai_llm_call)
        try:
            with (
                patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
                patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
            ):
                client = openai_sdk.OpenAI(
                    api_key="<test-key>",
                    http_client=httpx.Client(transport=_SyncMockTransport(_fake_chat_completion_response)),
                )
                client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": "hi"}])
        finally:
            core.reset_listeners("openai.chat.completions.create.before")

        mock_waf.assert_called_once_with({"LLM_EVENT": {"provider": "openai", "model": "gpt-4.1"}})

    def test_completions_calls_waf_callback(self, openai_sdk):
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        core.on("openai.completions.create.before", _on_openai_llm_call)
        try:
            with (
                patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
                patch("ddtrace.appsec._contrib.openai.handlers.call_waf_callback") as mock_waf,
            ):
                client = openai_sdk.OpenAI(
                    api_key="<test-key>",
                    http_client=httpx.Client(transport=_SyncMockTransport(_fake_completion_response)),
                )
                client.completions.create(model="gpt-3.5-turbo-instruct", prompt="hi")
        finally:
            core.reset_listeners("openai.completions.create.before")

        mock_waf.assert_called_once_with({"LLM_EVENT": {"provider": "openai", "model": "gpt-3.5-turbo-instruct"}})

    def test_handler_calls_waf_callback_on_each_invocation(self):
        """The handler forwards every LLM call to call_waf_callback; WAF processor dedup is downstream."""
        from ddtrace.appsec._contrib.openai.handlers import _on_openai_llm_call

        waf_calls = []
        with (
            patch("ddtrace.appsec._contrib.openai.handlers.in_asm_context", return_value=True),
            patch(
                "ddtrace.appsec._contrib.openai.handlers.call_waf_callback", side_effect=lambda d: waf_calls.append(d)
            ),
        ):
            _on_openai_llm_call({"model": "gpt-4.1"})
            _on_openai_llm_call({"model": "o1-mini"})

        assert len(waf_calls) == 2
        assert waf_calls[0] == {"LLM_EVENT": {"provider": "openai", "model": "gpt-4.1"}}
        assert waf_calls[1] == {"LLM_EVENT": {"provider": "openai", "model": "o1-mini"}}


# ---------------------------------------------------------------------------
# Integration: real ASM context + WAF rule → span tags
# ---------------------------------------------------------------------------


class TestWAFIntegration:
    """Verify the full path: call_waf_callback → WAF rule → span meta tags."""

    def test_waf_sets_span_tags_from_llm_event(self):
        from ddtrace.appsec._asm_request_context import call_waf_callback
        from tests.appsec.rules import RULES_LLM
        from tests.appsec.utils import asm_context

        config = {"_asm_static_rule_file": RULES_LLM, "_asm_enabled": True}
        with asm_context(config=config) as span:
            call_waf_callback({"LLM_EVENT": {"provider": "openai", "model": "gpt-4.1"}})

        assert span.get_tag("appsec.events.llm.call.provider") == "openai"
        assert span.get_tag("appsec.events.llm.call.model") == "gpt-4.1"

    def test_waf_keep_set_on_llm_event(self):
        """The LLM rule has keep:true, so sampling priority must be USER_KEEP."""
        from ddtrace.appsec._asm_request_context import call_waf_callback
        from ddtrace.constants import USER_KEEP
        from tests.appsec.rules import RULES_LLM
        from tests.appsec.utils import asm_context

        config = {"_asm_static_rule_file": RULES_LLM, "_asm_enabled": True}
        with asm_context(config=config) as span:
            call_waf_callback({"LLM_EVENT": {"provider": "openai", "model": "gpt-4.1"}})

        assert span.context.sampling_priority == USER_KEEP

    def test_waf_dedup_only_first_llm_call_per_request(self):
        """PERSISTENT_ADDRESSES dedup: only the first LLM call's model is surfaced on the span."""
        from ddtrace.appsec._asm_request_context import call_waf_callback
        from tests.appsec.rules import RULES_LLM
        from tests.appsec.utils import asm_context

        config = {"_asm_static_rule_file": RULES_LLM, "_asm_enabled": True}
        with asm_context(config=config) as span:
            call_waf_callback({"LLM_EVENT": {"provider": "openai", "model": "gpt-4.1"}})
            call_waf_callback({"LLM_EVENT": {"provider": "openai", "model": "o1-mini"}})

        # Only the first call's model reaches the WAF rule (persistent address dedup).
        assert span.get_tag("appsec.events.llm.call.model") == "gpt-4.1"


class TestRawResponseStreaming:
    """Test that raw-response + stream=True still fires the .before event."""

    def test_raw_response_streaming_dispatches_before_event(self, openai_sdk):
        received = []
        core.on("openai.completions.create.before", received.append)
        try:
            client = openai_sdk.OpenAI(
                api_key="<test-key>",
                http_client=httpx.Client(
                    transport=_SyncMockTransport(
                        lambda: httpx.Response(
                            200,
                            headers={"content-type": "text/event-stream"},
                            stream=httpx.ByteStream(
                                b"data: "
                                b'{"id":"cmpl-test","object":"text_completion",'
                                b'"choices":[{"text":"hi","finish_reason":null,"index":0}],'
                                b'"model":"gpt-3.5-turbo-instruct"}\n\n'
                                b"data: [DONE]\n\n"
                            ),
                        )
                    )
                ),
            )
            raw = client.completions.with_raw_response.create(model="gpt-3.5-turbo-instruct", prompt="hi", stream=True)
            for _ in raw.parse():
                pass
        except Exception:
            pass  # stream parsing may raise; .before was already dispatched
        finally:
            core.reset_listeners("openai.completions.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-3.5-turbo-instruct"

    def test_raw_response_chat_streaming_dispatches_before_event(self, openai_sdk):
        received = []
        core.on("openai.chat.completions.create.before", received.append)
        try:
            client = openai_sdk.OpenAI(
                api_key="<test-key>",
                http_client=httpx.Client(
                    transport=_SyncMockTransport(
                        lambda: httpx.Response(
                            200,
                            headers={"content-type": "text/event-stream"},
                            stream=httpx.ByteStream(_fake_stream_chunks()),
                        )
                    )
                ),
            )
            raw = client.chat.completions.with_raw_response.create(
                model="gpt-4.1", messages=[{"role": "user", "content": "hi"}], stream=True
            )
            for _ in raw.parse():
                pass
        except Exception:
            pass
        finally:
            core.reset_listeners("openai.chat.completions.create.before")

        assert len(received) == 1
        assert received[0].get("model") == "gpt-4.1"
