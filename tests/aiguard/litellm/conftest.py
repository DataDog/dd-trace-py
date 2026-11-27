"""Fixtures of the LiteLLM SDK tests: AI Guard verdicts, a recorder of the evaluations and an offline provider.
The LiteLLM and OpenAI integrations are patched for each test, as ddtrace-run does.
"""

import json
from typing import Any
from unittest.mock import patch

import httpx
import litellm
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm.llms.custom_httpx.http_handler import HTTPHandler
import pytest

from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard._context import is_aiguard_context_active
from ddtrace.aiguard._initialization import load_ai_guard
from ddtrace.contrib.internal.litellm.patch import patch as patch_litellm
from ddtrace.contrib.internal.litellm.patch import unpatch as unpatch_litellm
from ddtrace.contrib.internal.openai.patch import patch as patch_openai
from ddtrace.contrib.internal.openai.patch import unpatch as unpatch_openai
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


_AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-application-key",
)

PROMPT = "What is the weather in Paris?"
ANSWER = "Let me check."
TOOL_CALL_ID = "call_weather"
TOOL_NAME = "get_weather"
TOOL_ARGUMENTS = json.dumps({"city": "Paris"})
# The stub answers a request for this model with an error.
FAILING_MODEL = "gpt-failing"


def pytest_configure():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        load_ai_guard()


def _target(messages: list[Any]) -> str:
    return "answer" if messages[-1].get("role") == "assistant" else "prompt"


@pytest.fixture
def verdicts():
    """Give the AI Guard action for each target. Change it in a test to block a prompt or an answer.
    An exception makes the call to AI Guard fail; a callable gets the evaluated messages.
    """
    return {"prompt": "ALLOW", "answer": "ALLOW"}


@pytest.fixture
def evaluated_messages():
    """Record the messages of each AI Guard evaluation, in the order of the evaluations fixture."""
    return []


@pytest.fixture
def evaluation_kwargs():
    """Record the keyword arguments of each AI Guard evaluation, in the order of the evaluations fixture."""
    return []


@pytest.fixture(autouse=True)
def evaluations(verdicts, evaluated_messages, evaluation_kwargs):
    """Record (integration, target) for each AI Guard evaluation."""
    calls: list[tuple[Any, str]] = []
    evaluate = AIGuardClient.evaluate

    def record(self: AIGuardClient, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        calls.append((kwargs.get("integration"), _target(messages)))
        evaluated_messages.append(list(messages))
        evaluation_kwargs.append(kwargs)
        return evaluate(self, messages, *args, **kwargs)

    def verdict(url: str, payload: Any) -> Any:
        messages = payload["data"]["attributes"]["messages"]
        action = verdicts[_target(messages)]
        if callable(action):
            action = action(messages)
        if isinstance(action, Exception):
            raise action
        return mock_evaluate_response(action, reason="test", block=True)

    with (
        patch.object(AIGuardClient, "evaluate", record),
        patch.object(AIGuardClient, "_execute_request", side_effect=verdict),
    ):
        yield calls


@pytest.fixture(autouse=True)
def integrations():
    patch_litellm()
    patch_openai()
    try:
        yield
    finally:
        unpatch_openai()
        unpatch_litellm()


def _tool_call() -> dict[str, Any]:
    return {"id": TOOL_CALL_ID, "type": "function", "function": {"name": TOOL_NAME, "arguments": TOOL_ARGUMENTS}}


def _chat_completion(model: str) -> dict[str, Any]:
    message = {"role": "assistant", "content": ANSWER, "tool_calls": [_tool_call()]}
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def _text_completion(model: str, prompt: Any) -> dict[str, Any]:
    # The OpenAI API gives one choice for each prompt string.
    count = len(prompt) if isinstance(prompt, list) and all(isinstance(p, str) for p in prompt) else 1
    return {
        "id": "cmpl-test",
        "object": "text_completion",
        "created": 1700000000,
        "model": model,
        "choices": [{"index": i, "text": ANSWER, "logprobs": None, "finish_reason": "stop"} for i in range(count)],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def _anthropic_message(model: str) -> dict[str, Any]:
    tool_use = {"type": "tool_use", "id": TOOL_CALL_ID, "name": TOOL_NAME, "input": json.loads(TOOL_ARGUMENTS)}
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": ANSWER}, tool_use],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }


def _events(chunks: list[dict[str, Any]]) -> bytes:
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def _chat_chunks(model: str) -> list[dict[str, Any]]:
    def chunk(delta: dict[str, Any], finish_reason: Any = None) -> dict[str, Any]:
        choice = {"index": 0, "delta": delta, "finish_reason": finish_reason}
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": model,
            "choices": [choice],
        }

    return [chunk({"role": "assistant", "content": ANSWER}), chunk({}, finish_reason="stop")]


def _text_chunks(model: str) -> list[dict[str, Any]]:
    def chunk(text: str, finish_reason: Any = None) -> dict[str, Any]:
        choice = {"index": 0, "text": text, "logprobs": None, "finish_reason": finish_reason}
        return {
            "id": "cmpl-test",
            "object": "text_completion",
            "created": 1700000000,
            "model": model,
            "choices": [choice],
        }

    return [chunk(ANSWER), chunk("", finish_reason="stop")]


class ProviderStub:
    """This offline provider answers OpenAI chat and text completions and Anthropic messages, with a tool call.
    It records each request body, and whether an AI Guard claim was active when the request was sent.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.claimed: list[bool] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        self.claimed.append(is_aiguard_context_active())
        model = body.get("model", "")
        path = request.url.path
        stream_headers = {"content-type": "text/event-stream"}
        if model == FAILING_MODEL:
            return httpx.Response(400, json={"error": {"message": "stub error", "type": "invalid_request_error"}})
        if path.endswith("/v1/messages"):
            return httpx.Response(200, json=_anthropic_message(model))
        if path.endswith("/chat/completions"):
            if body.get("stream"):
                return httpx.Response(200, headers=stream_headers, content=_events(_chat_chunks(model)))
            return httpx.Response(200, json=_chat_completion(model))
        if path.endswith("/completions"):
            if body.get("stream"):
                return httpx.Response(200, headers=stream_headers, content=_events(_text_chunks(model)))
            return httpx.Response(200, json=_text_completion(model, body.get("prompt")))
        return httpx.Response(404, json={"error": {"message": f"no stub for {path}"}})

    def anthropic_client(self, is_async: bool) -> Any:
        """Return a litellm HTTP handler that sends the Anthropic calls to this stub."""
        if not is_async:
            return HTTPHandler(client=httpx.Client(transport=httpx.MockTransport(self)))
        handler = AsyncHTTPHandler()
        # The constructor of AsyncHTTPHandler always makes its own client.
        handler.client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return handler


@pytest.fixture(autouse=True)
def provider():
    """Send the calls that litellm makes with the OpenAI SDK to the stub."""
    stub = ProviderStub()
    sessions = litellm.client_session, litellm.aclient_session
    litellm.client_session = httpx.Client(transport=httpx.MockTransport(stub))
    litellm.aclient_session = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    litellm.in_memory_llm_clients_cache.flush_cache()
    try:
        yield stub
    finally:
        litellm.in_memory_llm_clients_cache.flush_cache()
        litellm.client_session, litellm.aclient_session = sessions
