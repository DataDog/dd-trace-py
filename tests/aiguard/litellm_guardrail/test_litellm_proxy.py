"""These tests send requests through the LiteLLM proxy app, which runs in this process.

The tests patch the OpenAI integration and send the OpenAI calls of LiteLLM to an offline stub.
"""

import asyncio
import json
from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import httpx
import litellm
from litellm import Router
from litellm.proxy import proxy_server
import openai
import pytest

from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard._context import is_aiguard_context_active
from ddtrace.aiguard.integrations.litellm import DatadogAIGuardGuardrail
from ddtrace.contrib.internal.openai.patch import patch as patch_openai
from ddtrace.contrib.internal.openai.patch import unpatch as unpatch_openai
from tests.aiguard.litellm_guardrail.conftest import _AI_GUARD_CONFIG
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


# All tests use one event loop, because LiteLLM keeps asyncio objects (for example its logging queue) between requests.
pytestmark = pytest.mark.asyncio(loop_scope="module")

MODEL = "gpt-4o-mini"
ANSWER_PARTS = ["Hello", " there!"]
ANSWER = "".join(ANSWER_PARTS)
PROMPT = "Say hello"

# LiteLLM keeps its callbacks in these module lists and adds to them on each call.
_CALLBACK_LISTS = (
    "callbacks",
    "input_callback",
    "success_callback",
    "failure_callback",
    "_async_input_callback",
    "_async_success_callback",
    "_async_failure_callback",
    "service_callback",
)


def _chat_completion() -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _chat_completion_events() -> bytes:
    def chunk(delta: dict[str, Any], finish_reason: Any = None) -> dict[str, Any]:
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    chunks = [chunk({"role": "assistant", "content": ""})]
    chunks += [chunk({"content": part}) for part in ANSWER_PARTS]
    chunks.append(chunk({}, finish_reason="stop"))
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


class OpenAIStub:
    """This offline OpenAI API answers chat completions as JSON, or as server-sent events for streams."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": f"no stub for {request.url.path}"}})
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_chat_completion_events())
        return httpx.Response(200, json=_chat_completion())


def _target(messages: list[Any]) -> str:
    return "answer" if messages[-1].get("role") == "assistant" else "prompt"


@pytest.fixture
def verdicts():
    """This fixture gives the AI Guard action for each target. Change it in a test to block a prompt or an answer."""
    return {"prompt": "ALLOW", "answer": "ALLOW"}


@pytest.fixture
def evaluations(verdicts):
    """Record (integration, target) for each AI Guard evaluation."""
    calls: list[tuple[Any, str]] = []
    evaluate = AIGuardClient.evaluate

    def record(self: AIGuardClient, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        calls.append((kwargs.get("integration"), _target(messages)))
        return evaluate(self, messages, *args, **kwargs)

    def verdict(url: str, payload: Any) -> Mock:
        action = verdicts[_target(payload["data"]["attributes"]["messages"])]
        return mock_evaluate_response(action, reason="test", block=True)

    with (
        patch.object(AIGuardClient, "evaluate", record),
        patch.object(AIGuardClient, "_execute_request", side_effect=verdict),
    ):
        yield calls


@pytest.fixture
def openai_stub():
    """Patch the OpenAI integration, and send the OpenAI calls of LiteLLM to the stub."""
    stub = OpenAIStub()
    session = litellm.aclient_session
    patch_openai()
    litellm.aclient_session = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    litellm.in_memory_llm_clients_cache.flush_cache()
    try:
        yield stub
    finally:
        litellm.in_memory_llm_clients_cache.flush_cache()
        litellm.aclient_session = session
        unpatch_openai()


@pytest.fixture
def litellm_proxy(openai_stub, evaluations):
    """Configure the proxy with one OpenAI deployment. Call the fixture value to add the guardrail."""
    saved_lists = {name: list(getattr(litellm, name)) for name in _CALLBACK_LISTS if hasattr(litellm, name)}
    saved_router = proxy_server.llm_router
    proxy_server.llm_router = Router(
        model_list=[
            {
                "model_name": MODEL,
                "litellm_params": {
                    "model": f"openai/{MODEL}",
                    "api_key": "sk-test",
                    "api_base": "http://openai.test/v1",
                },
            }
        ]
    )

    def add_guardrail(event_hook: tuple[str, ...] = ("pre_call", "post_call")) -> DatadogAIGuardGuardrail:
        with override_ai_guard_config(_AI_GUARD_CONFIG):
            guardrail = DatadogAIGuardGuardrail(block=True, event_hook=list(event_hook), default_on=True)
        litellm.callbacks.append(guardrail)
        return guardrail

    try:
        yield add_guardrail
    finally:
        proxy_server.llm_router = saved_router
        for name, saved in saved_lists.items():
            setattr(litellm, name, saved)


async def _settle() -> None:
    """Wait for the tasks that the proxy starts in the background, for example its deferred guardrails."""
    current = asyncio.current_task()
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not current and not getattr(task.get_coro(), "__qualname__", "").endswith("_worker_loop")
    ]
    if pending:
        await asyncio.wait(pending, timeout=5)


async def _post(path: str, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_server.app), base_url="http://proxy.test"
    ) as client:
        response = await client.post(path, json=body)
    await _settle()
    return response


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[len("data: ") :]))
    return events


ONE_PROMPT_AND_ONE_ANSWER = [("litellm", "prompt"), ("litellm", "answer")]


async def test_chat_completion(litellm_proxy, openai_stub, evaluations):
    litellm_proxy()

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert len(openai_stub.requests) == 1
    assert not is_aiguard_context_active()


async def test_chat_completion_stream(litellm_proxy, openai_stub, evaluations):
    litellm_proxy()

    response = await _post(
        "/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "stream": True}
    )

    assert response.status_code == 200, response.text
    contents = [event["choices"][0]["delta"].get("content") for event in _events(response) if event["choices"]]
    assert "".join(c for c in contents if c) == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert len(openai_stub.requests) == 1
    assert not is_aiguard_context_active()


async def test_text_completion(litellm_proxy, openai_stub, evaluations):
    litellm_proxy()

    response = await _post("/completions", {"model": MODEL, "prompt": PROMPT})

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["text"] == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    # LiteLLM sends the text completion of a chat model as a chat completion.
    assert openai_stub.requests[0]["messages"] == [{"role": "user", "content": PROMPT}]
    assert not is_aiguard_context_active()


async def test_text_completion_stream(litellm_proxy, openai_stub, evaluations):
    litellm_proxy()

    response = await _post("/completions", {"model": MODEL, "prompt": PROMPT, "stream": True})

    assert response.status_code == 200, response.text
    texts = [event["choices"][0].get("text") for event in _events(response) if event["choices"]]
    assert "".join(t for t in texts if t) == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert len(openai_stub.requests) == 1
    assert not is_aiguard_context_active()


async def test_blocked_prompt(litellm_proxy, openai_stub, evaluations, verdicts):
    litellm_proxy()
    verdicts["prompt"] = "DENY"

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 403, response.text
    assert evaluations == [("litellm", "prompt")]
    assert openai_stub.requests == []
    assert not is_aiguard_context_active()


async def test_blocked_streamed_answer(litellm_proxy, openai_stub, evaluations, verdicts):
    litellm_proxy()
    verdicts["answer"] = "DENY"

    response = await _post(
        "/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "stream": True}
    )

    assert response.status_code == 403, response.text
    assert "Datadog AI Guard: request blocked" in response.text
    assert "Traceback" not in response.text
    assert "chat.completion.chunk" not in response.text
    assert ANSWER_PARTS[-1] not in response.text
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert not is_aiguard_context_active()


async def test_post_call_only_lets_the_openai_integration_evaluate_the_prompt(litellm_proxy, evaluations):
    # Without the pre-call check, there is no claim. This test shows that the OpenAI listeners see the proxy calls.
    litellm_proxy(event_hook=("post_call",))

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 200, response.text
    assert evaluations == [("openai", "prompt"), ("litellm", "answer")]


async def test_pre_call_only_mode_does_not_claim(litellm_proxy, evaluations):
    # No post-call hook evaluates the answer in this mode, so the OpenAI listeners keep their evaluations.
    litellm_proxy(event_hook=("pre_call",))

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 200, response.text
    assert evaluations == [("litellm", "prompt"), ("openai", "prompt")]
    assert not is_aiguard_context_active()


async def test_messages_api_does_not_claim(litellm_proxy, openai_stub, evaluations, monkeypatch):
    # The post-call hooks do not evaluate Anthropic answers, so the OpenAI listeners keep their evaluations.
    litellm_proxy()
    # By default, litellm 1.82 sends this request to the Responses API with its own HTTP client.
    # This setting makes it a chat completion through the OpenAI SDK, like litellm 1.78 always does.
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)

    response = await _post(
        "/v1/messages", {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": PROMPT}]}
    )

    assert response.status_code == 200, response.text
    # LiteLLM sends the Anthropic request of an OpenAI model as a chat completion.
    assert openai_stub.requests[0]["messages"] == [{"role": "user", "content": PROMPT}]
    assert evaluations == [("litellm", "prompt"), ("openai", "prompt")]
    assert not is_aiguard_context_active()


async def test_plain_openai_calls_outside_the_proxy(openai_stub, evaluations):
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        guardrail = DatadogAIGuardGuardrail(block=True, event_hook=["pre_call", "post_call"], default_on=True)
    client = openai.AsyncOpenAI(
        api_key="sk-test",
        base_url="http://openai.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(openai_stub)),
    )
    messages = [{"role": "user", "content": PROMPT}]

    await client.chat.completions.create(model=MODEL, messages=messages)
    # Outside the proxy, for example with the SDK guardrails argument, the hook runs in the caller task.
    await guardrail.async_pre_call_hook(
        user_api_key_dict=Mock(), cache=Mock(), data={"model": MODEL, "messages": messages}, call_type="acompletion"
    )
    await client.chat.completions.create(model=MODEL, messages=messages)

    assert evaluations == [
        ("openai", "prompt"),
        ("openai", "answer"),
        ("litellm", "prompt"),
        ("openai", "prompt"),
        ("openai", "answer"),
    ]
