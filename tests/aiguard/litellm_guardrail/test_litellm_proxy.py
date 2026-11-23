"""These tests send requests through the LiteLLM proxy app, which runs in this process.

The tests patch the OpenAI integration and send the model calls of LiteLLM to an offline stub.
"""

import asyncio
import json
from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import httpx
import litellm
from litellm import Router
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
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
CLAUDE = "claude-3-5-haiku-20241022"
ANSWER_PARTS = ["Hello", " there!"]
ANSWER = "".join(ANSWER_PARTS)
PROMPT = "Say hello"
# litellm 1.82 sends the /v1/messages requests of OpenAI models to the Responses API, unless this setting is True.
_MESSAGES_TO_RESPONSES = hasattr(litellm, "use_chat_completions_url_for_anthropic_messages")

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


def _embeddings() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
        "model": MODEL,
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }


def _output_message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "msg_test",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _response(output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1700000000,
        "status": "completed",
        "model": MODEL,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }


def _anthropic_message() -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": CLAUDE,
        "content": [{"type": "text", "text": ANSWER}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


class OpenAIStub:
    """This offline API answers OpenAI embeddings, chat completions and responses, and Anthropic messages.

    Chat streams use server-sent events. The output field sets the output items of the responses.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.output: list[dict[str, Any]] = [_output_message(ANSWER)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        self.paths.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json=_embeddings())
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, json=_response(self.output))
        if request.url.path.endswith("/v1/messages"):
            return httpx.Response(200, json=_anthropic_message())
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": f"no stub for {request.url.path}"}})
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_chat_completion_events())
        return httpx.Response(200, json=_chat_completion())


def _target(messages: list[Any]) -> str:
    return "answer" if messages[-1].get("role") == "assistant" else "prompt"


@pytest.fixture
def verdicts():
    """This fixture gives the AI Guard action for each target. Change it in a test to block a prompt or an answer.

    A list gives one action for each call, and an exception makes the call to AI Guard fail.
    """
    # A value can also be a function that gets the evaluated messages and gives the action.
    return {"prompt": "ALLOW", "answer": "ALLOW"}


@pytest.fixture
def evaluated_messages():
    """Record the messages of each AI Guard evaluation, in the order of the evaluations fixture."""
    return []


@pytest.fixture
def evaluated_payloads():
    """Record the payload of each AI Guard evaluation request."""
    return []


@pytest.fixture
def evaluations(verdicts, evaluated_messages, evaluated_payloads):
    """Record (integration, target) for each AI Guard evaluation."""
    calls: list[tuple[Any, str]] = []
    evaluate = AIGuardClient.evaluate

    def record(self: AIGuardClient, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        calls.append((kwargs.get("integration"), _target(messages)))
        evaluated_messages.append(list(messages))
        return evaluate(self, messages, *args, **kwargs)

    def verdict(url: str, payload: Any) -> Mock:
        evaluated_payloads.append(payload)
        action = verdicts[_target(payload["data"]["attributes"]["messages"])]
        if callable(action):
            action = action(payload["data"]["attributes"]["messages"])
        if isinstance(action, list):
            action = action.pop(0)
        if isinstance(action, Exception):
            raise action
        return mock_evaluate_response(action, reason="test", block=True)

    with (
        patch.object(AIGuardClient, "evaluate", record),
        patch.object(AIGuardClient, "_execute_request", side_effect=verdict),
    ):
        yield calls


@pytest.fixture
def openai_stub():
    """Patch the OpenAI integration, and send the model calls of LiteLLM to the stub."""
    stub = OpenAIStub()
    session = litellm.aclient_session
    patch_openai()
    litellm.aclient_session = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    litellm.in_memory_llm_clients_cache.flush_cache()
    # LiteLLM sends the Responses API and the Anthropic calls with its own HTTP client, not with the SDKs.
    mock_transport = staticmethod(lambda *args, **kwargs: httpx.MockTransport(stub))
    try:
        with patch.object(AsyncHTTPHandler, "_create_async_transport", mock_transport):
            yield stub
    finally:
        litellm.in_memory_llm_clients_cache.flush_cache()
        litellm.aclient_session = session
        unpatch_openai()


@pytest.fixture
def stream_analysis():
    """This fixture gives the value of DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED for the guardrail.

    Parametrize it to change it.
    """
    return False


@pytest.fixture
def litellm_proxy(openai_stub, evaluations, stream_analysis):
    """Configure the proxy with an OpenAI and an Anthropic deployment. Call the fixture value to add the guardrail."""
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
            },
            {
                "model_name": CLAUDE,
                "litellm_params": {
                    "model": f"anthropic/{CLAUDE}",
                    "api_key": "sk-ant-test",
                    "api_base": "http://anthropic.test",
                },
            },
        ]
    )

    def add_guardrail(event_hook: tuple[str, ...] = ("pre_call", "post_call")) -> DatadogAIGuardGuardrail:
        with override_ai_guard_config(_AI_GUARD_CONFIG):
            guardrail = DatadogAIGuardGuardrail(block=True, event_hook=list(event_hook), default_on=True)
        litellm.callbacks.append(guardrail)
        return guardrail

    # Set after the OpenAI patch, so the setting changes only the guardrail. At patch time, it also adds
    # OpenAI raw-response wrappers, and litellm streams fail with them.
    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=stream_analysis)):
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


@pytest.mark.parametrize("stream_analysis", [False, True])
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


async def test_text_completion(litellm_proxy, openai_stub, evaluations, evaluated_messages):
    litellm_proxy()

    response = await _post("/completions", {"model": MODEL, "prompt": PROMPT})

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["text"] == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert evaluated_messages[1] == [{"role": "user", "content": PROMPT}, {"role": "assistant", "content": ANSWER}]
    # LiteLLM sends the text completion of a chat model as a chat completion.
    assert openai_stub.requests[0]["messages"] == [{"role": "user", "content": PROMPT}]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("stream_analysis", [False, True])
async def test_text_completion_stream(litellm_proxy, openai_stub, evaluations):
    litellm_proxy()

    response = await _post("/completions", {"model": MODEL, "prompt": PROMPT, "stream": True})

    assert response.status_code == 200, response.text
    texts = [event["choices"][0].get("text") for event in _events(response) if event["choices"]]
    assert "".join(t for t in texts if t) == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert len(openai_stub.requests) == 1
    assert not is_aiguard_context_active()


async def test_client_metadata_cannot_skip_the_answer_evaluation(litellm_proxy, evaluations, verdicts):
    # The request body sets the metadata, so a key there must not switch off the answer evaluation.
    litellm_proxy()
    verdicts["answer"] = "DENY"

    response = await _post(
        "/chat/completions",
        {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "metadata": {"ai_guard_stream": True}},
    )

    assert response.status_code == 403, response.text
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert not is_aiguard_context_active()


async def test_blocked_prompt(litellm_proxy, openai_stub, evaluations, verdicts):
    litellm_proxy()
    verdicts["prompt"] = "DENY"

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 403, response.text
    assert evaluations == [("litellm", "prompt")]
    assert openai_stub.requests == []
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("stream_analysis", [True])
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


async def test_denied_live_streamed_answer_is_only_reported(litellm_proxy, evaluations, verdicts):
    # With stream analysis off, the client gets the chunks before AI Guard evaluates the answer.
    litellm_proxy()
    verdicts["answer"] = "DENY"

    response = await _post(
        "/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "stream": True}
    )

    assert response.status_code == 200, response.text
    contents = [event["choices"][0]["delta"].get("content") for event in _events(response) if event["choices"]]
    assert "".join(c for c in contents if c) == ANSWER
    assert "error" not in response.text
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert not is_aiguard_context_active()


async def test_failed_prompt_evaluation_lets_the_openai_integration_try_again(litellm_proxy, evaluations, verdicts):
    # The guardrail claims only after a completed evaluation, so the OpenAI listener evaluates the prompt again.
    litellm_proxy()
    verdicts["prompt"] = [OSError("AI Guard is not available"), "ALLOW"]

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 200, response.text
    assert evaluations == [("litellm", "prompt"), ("openai", "prompt"), ("litellm", "answer")]
    assert not is_aiguard_context_active()


async def test_post_call_only_lets_the_openai_integration_evaluate_the_prompt(litellm_proxy, evaluations):
    # Without the pre-call check, there is no claim. This test shows that the OpenAI listeners see the proxy calls.
    litellm_proxy(event_hook=("post_call",))

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 200, response.text
    assert evaluations == [("openai", "prompt"), ("litellm", "answer")]


@pytest.mark.parametrize("stream, stream_analysis", [(False, False), (True, False), (True, True)])
async def test_post_call_only_evaluates_the_answer_with_the_prompt(
    litellm_proxy, evaluations, evaluated_messages, stream
):
    # Without the pre-call check, the guardrail gets the prompt from the request for the answer evaluation.
    litellm_proxy(event_hook=("post_call",))

    response = await _post(
        "/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "stream": stream}
    )

    assert response.status_code == 200, response.text
    assert evaluations == [("openai", "prompt"), ("litellm", "answer")]
    assert evaluated_messages[1] == [{"role": "user", "content": PROMPT}, {"role": "assistant", "content": ANSWER}]


async def test_pre_call_only_mode_does_not_claim(litellm_proxy, evaluations):
    # No post-call hook evaluates the answer in this mode, so the OpenAI listeners keep their evaluations.
    litellm_proxy(event_hook=("pre_call",))

    response = await _post("/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]})

    assert response.status_code == 200, response.text
    assert evaluations == [("litellm", "prompt"), ("openai", "prompt")]
    assert not is_aiguard_context_active()


SYSTEM = "You answer in English."
TOOL_RESULT = "Say hello in English"
# This /v1/messages body has a system prompt, a tool call of the model and the result of the tool.
MESSAGES_BODY = {
    "max_tokens": 64,
    "system": SYSTEM,
    "tools": [
        {
            "name": "lookup",
            "description": "Look up a phrase.",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ],
    "messages": [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"query": "hello"}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": TOOL_RESULT}]},
    ],
}
# The guardrail evaluates MESSAGES_BODY as these messages.
MESSAGES_PROMPT = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": PROMPT},
    {
        "role": "assistant",
        "tool_calls": [{"id": "toolu_1", "function": {"name": "lookup", "arguments": '{"query": "hello"}'}}],
    },
    {"role": "tool", "tool_call_id": "toolu_1", "content": TOOL_RESULT},
]
# The model, the chat completions setting and the upstream path of each provider path of /v1/messages.
_MESSAGES_PATHS = [
    pytest.param(MODEL, True, "/v1/chat/completions", id="openai-chat"),
    pytest.param(
        MODEL, False, "/v1/responses" if _MESSAGES_TO_RESPONSES else "/v1/chat/completions", id="openai-default"
    ),
    pytest.param(CLAUDE, False, "/v1/messages", id="anthropic"),
]


@pytest.mark.parametrize("model, chat_setting, upstream", _MESSAGES_PATHS)
async def test_messages_api(
    litellm_proxy, openai_stub, evaluations, evaluated_messages, monkeypatch, model, chat_setting, upstream
):
    guardrail = litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", chat_setting, raising=False)

    response = await _post("/v1/messages", {"model": model, **MESSAGES_BODY})

    assert response.status_code == 200, response.text
    assert response.json()["content"][0]["text"] == ANSWER
    assert openai_stub.paths == [upstream]
    # The guardrail claims the request, so the OpenAI listener does not evaluate the prompt of the chat completion.
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert evaluated_messages == [MESSAGES_PROMPT, MESSAGES_PROMPT + [{"role": "assistant", "content": ANSWER}]]
    # The client sends no metadata. The response header must still show the guardrail.
    assert guardrail.guardrail_name in response.headers.get("x-litellm-applied-guardrails", "").split(",")
    assert not is_aiguard_context_active()


async def test_responses_api(litellm_proxy, openai_stub, evaluations, evaluated_messages):
    litellm_proxy()

    response = await _post("/v1/responses", {"model": MODEL, "input": PROMPT})

    assert response.status_code == 200, response.text
    assert response.json()["output"][0]["content"][0]["text"] == ANSWER
    # LiteLLM sends the Responses API call with its own HTTP client.
    assert openai_stub.paths == ["/v1/responses"]
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert evaluated_messages[1] == [{"role": "user", "content": PROMPT}, {"role": "assistant", "content": ANSWER}]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize(
    "path, body, chat_setting",
    [
        pytest.param("/v1/messages", {"model": MODEL, **MESSAGES_BODY}, True, id="messages-openai-chat"),
        pytest.param("/v1/messages", {"model": MODEL, **MESSAGES_BODY}, False, id="messages-openai-default"),
        pytest.param("/v1/messages", {"model": CLAUDE, **MESSAGES_BODY}, False, id="messages-anthropic"),
        pytest.param("/v1/responses", {"model": MODEL, "input": PROMPT}, False, id="responses"),
    ],
)
async def test_blocked_answer(litellm_proxy, evaluations, verdicts, monkeypatch, path, body, chat_setting):
    litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", chat_setting, raising=False)
    verdicts["answer"] = "DENY"

    response = await _post(path, body)

    assert response.status_code == 403, response.text
    assert "Datadog AI Guard: request blocked" in response.text
    assert ANSWER not in response.text
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("model", [MODEL, CLAUDE])
async def test_blocked_messages_prompt(
    litellm_proxy, openai_stub, evaluations, evaluated_messages, verdicts, monkeypatch, model
):
    litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)
    verdicts["prompt"] = "DENY"

    response = await _post("/v1/messages", {"model": model, **MESSAGES_BODY})

    assert response.status_code == 403, response.text
    assert "Datadog AI Guard: request blocked" in response.text
    assert evaluations == [("litellm", "prompt")]
    # AI Guard gets the tool result, so it can block a prompt injection there before the model call.
    assert evaluated_messages == [MESSAGES_PROMPT]
    assert openai_stub.requests == []
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("model", [MODEL, CLAUDE])
async def test_blocked_messages_prompt_that_the_anthropic_converter_cannot_read(
    litellm_proxy, openai_stub, evaluations, evaluated_messages, verdicts, monkeypatch, model
):
    litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)
    verdicts["prompt"] = "DENY"
    # The Anthropic converter fails on a system text that is not a string, so the guardrail uses its chat conversion.
    body = {**MESSAGES_BODY, "system": [{"type": "text", "text": 5}]}

    response = await _post("/v1/messages", {"model": model, **body})

    assert response.status_code == 403, response.text
    assert "Datadog AI Guard: request blocked" in response.text
    assert evaluations == [("litellm", "prompt")]
    assert evaluated_messages[0][0] == {"role": "user", "content": PROMPT}
    assert openai_stub.requests == []
    assert not is_aiguard_context_active()


# litellm's chat adapter skips a block whose type is a list, but the Anthropic converter cannot read it.
UNREADABLE_MESSAGES = [
    {"role": "user", "content": [{"type": "text", "text": PROMPT}, {"type": []}]},
    *MESSAGES_BODY["messages"][1:],
]
# The chat conversion of UNREADABLE_MESSAGES, which the guardrail evaluates instead.
UNREADABLE_PROMPT = [
    {"role": "user", "content": [{"type": "text", "text": PROMPT}]},
    {"role": "assistant"},
    {"role": "user"},
]


async def test_unreadable_messages_body_is_not_claimed(
    litellm_proxy, openai_stub, evaluations, evaluated_messages, monkeypatch
):
    litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)

    response = await _post("/v1/messages", {"model": MODEL, **MESSAGES_BODY, "messages": UNREADABLE_MESSAGES})

    assert response.status_code == 200, response.text
    assert openai_stub.paths == ["/v1/chat/completions"]
    # The chat conversion misses the system prompt and the tool blocks, so the OpenAI listener also checks the prompt.
    assert evaluations == [("litellm", "prompt"), ("openai", "prompt"), ("litellm", "answer")]
    assert evaluated_messages[0] == UNREADABLE_PROMPT
    assert evaluated_messages[1][-1] == {"role": "tool", "tool_call_id": "toolu_1", "content": TOOL_RESULT}
    assert SYSTEM in json.dumps(evaluated_messages[1])
    assert "lookup" in json.dumps(evaluated_messages[1])
    assert evaluated_messages[2] == UNREADABLE_PROMPT + [{"role": "assistant", "content": ANSWER}]
    assert not is_aiguard_context_active()


async def test_tool_result_of_an_unreadable_messages_body_can_block(
    litellm_proxy, openai_stub, evaluations, evaluated_messages, verdicts, monkeypatch
):
    litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)
    verdicts["prompt"] = lambda messages: "DENY" if TOOL_RESULT in json.dumps(messages) else "ALLOW"

    response = await _post("/v1/messages", {"model": MODEL, **MESSAGES_BODY, "messages": UNREADABLE_MESSAGES})

    # The OpenAI listener blocks the model call: litellm 1.78.5 retries it and returns 500, and 1.82.6 returns 400.
    assert response.status_code in (400, 500), response.text
    assert "AIGuardAbortError" in response.text
    assert openai_stub.requests == []
    assert evaluations[0] == ("litellm", "prompt")
    assert set(evaluations[1:]) == {("openai", "prompt")}
    assert TOOL_RESULT in json.dumps(evaluated_messages[1])
    assert not is_aiguard_context_active()


ASSISTANT_TEXT = "I will look it up."
# litellm sends the string items of these content lists to a chat model as text.
STRING_ITEMS_MESSAGES = [
    {"role": "user", "content": PROMPT},
    {
        "role": "assistant",
        "content": [
            ASSISTANT_TEXT,
            {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"query": "hello"}},
        ],
    },
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [TOOL_RESULT]}]},
]
STRING_ITEMS_PROMPT = MESSAGES_PROMPT[:2] + [{**MESSAGES_PROMPT[2], "content": ASSISTANT_TEXT}, MESSAGES_PROMPT[3]]


async def test_messages_string_items_are_evaluated(
    litellm_proxy, openai_stub, evaluations, evaluated_messages, monkeypatch
):
    litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)

    response = await _post("/v1/messages", {"model": MODEL, **MESSAGES_BODY, "messages": STRING_ITEMS_MESSAGES})

    assert response.status_code == 200, response.text
    assert openai_stub.paths == ["/v1/chat/completions"]
    forwarded = json.dumps(openai_stub.requests[0]["messages"])
    assert ASSISTANT_TEXT in forwarded
    assert TOOL_RESULT in forwarded
    # The OpenAI listener skips the claimed request, so the guardrail must evaluate these texts.
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert evaluated_messages == [STRING_ITEMS_PROMPT, STRING_ITEMS_PROMPT + [{"role": "assistant", "content": ANSWER}]]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("model", [MODEL, CLAUDE])
async def test_post_call_only_evaluates_the_messages_answer_with_the_prompt(
    litellm_proxy, openai_stub, evaluations, evaluated_messages, monkeypatch, model
):
    litellm_proxy(event_hook=("post_call",))
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)

    response = await _post("/v1/messages", {"model": model, **MESSAGES_BODY})

    assert response.status_code == 200, response.text
    # Without the pre-call check there is no claim, so the OpenAI listener evaluates the prompt of a chat completion.
    listener = [("openai", "prompt")] if openai_stub.paths == ["/v1/chat/completions"] else []
    assert evaluations == listener + [("litellm", "answer")]
    assert evaluated_messages[-1] == MESSAGES_PROMPT + [{"role": "assistant", "content": ANSWER}]


async def test_streamed_messages_answer_passes_through(litellm_proxy, openai_stub, evaluations, monkeypatch):
    litellm_proxy()
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)

    response = await _post("/v1/messages", {"model": MODEL, **MESSAGES_BODY, "stream": True})

    assert response.status_code == 200, response.text
    texts = [event["delta"].get("text") for event in _events(response) if event.get("type") == "content_block_delta"]
    assert "".join(t for t in texts if t) == ANSWER
    assert openai_stub.paths == ["/v1/chat/completions"]
    # The OpenAI listener skips the claimed request, and the guardrail does not evaluate streamed Anthropic answers.
    assert evaluations == [("litellm", "prompt")]
    assert not is_aiguard_context_active()


# These hosted MCP vectors are copies of the ones of tests/aiguard/openai/test_responses.py.
MCP_TOOLS = [
    {
        "type": "mcp",
        "server_label": "deepwiki",
        "server_url": "https://user:secret@MCP.DeepWiki.com:443/mcp?token=abc#frag",
        "require_approval": "always",
        "authorization": "oauth-secret",
        "headers": {"X-Api-Key": "header-secret"},
    }
]
DEEPWIKI_ID = "mcp_58c928ebb7fc7a85"
DEEPWIKI_SERVER = {
    "id": DEEPWIKI_ID,
    "managed_by": "provider",
    "name": "deepwiki",
    "url": "https://mcp.deepwiki.com",
    "transport": "unknown",
    "tools": {"ask_question": "ask_question"},
}
SECRETS = ("secret", "token", "frag", "user:", "X-Api-Key", "oauth")
MCP_OUTPUT = [
    {
        "type": "mcp_list_tools",
        "id": "mcpl_1",
        "server_label": "deepwiki",
        "tools": [{"name": "ask_question", "input_schema": {"type": "object"}}],
    },
    {
        "type": "mcp_call",
        "id": "mcp_1",
        "server_label": "deepwiki",
        "name": "ask_question",
        "arguments": '{"repoName":"DataDog/dd-trace-py"}',
        "output": "It is the Python tracer.",
    },
    {
        "type": "mcp_approval_request",
        "id": "mcpr_1",
        "server_label": "deepwiki",
        "name": "ask_question",
        "arguments": '{"repoName":"DataDog/dd-trace-js"}',
    },
    _output_message(ANSWER),
]


async def test_responses_answer_with_hosted_mcp_items(litellm_proxy, openai_stub, evaluations, evaluated_payloads):
    litellm_proxy()
    openai_stub.output = MCP_OUTPUT

    response = await _post("/v1/responses", {"model": MODEL, "input": PROMPT, "tools": MCP_TOOLS})

    assert response.status_code == 200, response.text
    assert openai_stub.paths == ["/v1/responses"]
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    answer = evaluated_payloads[1]["data"]["attributes"]
    assert answer["mcp_servers"] == [DEEPWIKI_SERVER]
    tool_calls = [tool_call for message in answer["messages"] for tool_call in message.get("tool_calls") or []]
    assert [(tool_call["id"], tool_call["mcp"]) for tool_call in tool_calls] == [
        ("mcp_1", {"server_id": DEEPWIKI_ID}),
        ("mcpr_1", {"server_id": DEEPWIKI_ID}),
    ]
    reported = json.dumps(evaluated_payloads)
    assert [secret for secret in SECRETS if secret in reported] == []
    assert not is_aiguard_context_active()


@pytest.mark.parametrize(
    "path, body",
    [
        ("/completions", {"model": MODEL, "prompt": PROMPT}),
        ("/v1/messages", {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": PROMPT}]}),
    ],
)
async def test_during_call_evaluates_the_prompt(
    litellm_proxy, evaluations, evaluated_messages, monkeypatch, path, body
):
    # Some litellm versions send no call type to the during_call hook of these routes.
    litellm_proxy(event_hook=("during_call",))
    monkeypatch.setattr(litellm, "use_chat_completions_url_for_anthropic_messages", True, raising=False)

    response = await _post(path, body)

    assert response.status_code == 200, response.text
    assert ("litellm", "prompt") in evaluations
    assert evaluated_messages[evaluations.index(("litellm", "prompt"))] == [{"role": "user", "content": PROMPT}]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("event_hook", [("pre_call", "post_call"), ("during_call",)])
async def test_embeddings_are_not_evaluated(litellm_proxy, openai_stub, evaluations, event_hook):
    # Some litellm versions send the call type "embeddings" to the hooks. This value is not a CallTypes value.
    litellm_proxy(event_hook=event_hook)

    response = await _post("/embeddings", {"model": MODEL, "input": PROMPT})

    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["embedding"] == [0.1, 0.2]
    assert len(openai_stub.requests) == 1
    assert evaluations == []
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
