import json
import os

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


def _fake_chat_response_json():
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-3.5-turbo",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you?",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _fake_stream_chunks():
    chunks = [
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {"role": "assistant"}}]},
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {"content": "Hi"}}]},
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    lines = []
    for chunk in chunks:
        lines.append(b"data: " + json.dumps(chunk).encode() + b"\n\n")
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


class _MockTransport(httpx.BaseTransport):
    def handle_request(self, request):
        is_stream = False
        try:
            body = json.loads(request.content)
            is_stream = body.get("stream", False)
        except Exception:
            pass

        if is_stream:
            return httpx.Response(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                stream=httpx.ByteStream(_fake_stream_chunks()),
            )
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            json=_fake_chat_response_json(),
        )


class _MockAsyncTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        is_stream = False
        try:
            body = json.loads(request.content)
            is_stream = body.get("stream", False)
        except Exception:
            pass

        if is_stream:
            return httpx.Response(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                stream=httpx.ByteStream(_fake_stream_chunks()),
            )
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            json=_fake_chat_response_json(),
        )


@pytest.fixture
def openai_sdk():
    with override_env(
        dict(
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
        )
    ):
        patch()
        import openai

        yield openai
        unpatch()


@pytest.fixture
def openai_client(openai_sdk):
    return openai_sdk.OpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=_MockTransport()),
    )


@pytest.fixture
def async_openai_client(openai_sdk):
    return openai_sdk.AsyncOpenAI(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=_MockAsyncTransport()),
    )
