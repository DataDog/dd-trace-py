"""These tests call the LiteLLM SDK, which sends its provider requests to an offline stub."""

import logging
from typing import Any
from unittest import mock

import litellm
from litellm import Router
import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard import _listener
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._context import aiguard_context
from ddtrace.aiguard._context import is_aiguard_context_active
from ddtrace.aiguard.integrations._litellm import LiteLLMCallSubscriber
from ddtrace.contrib.internal.litellm.utils import _litellm_sends_openai_chat_with_post
from ddtrace.internal import core
from tests.aiguard.litellm.conftest import _AI_GUARD_CONFIG
from tests.aiguard.litellm.conftest import ANSWER
from tests.aiguard.litellm.conftest import FAILING_MODEL
from tests.aiguard.litellm.conftest import PROMPT
from tests.aiguard.litellm.conftest import TOOL_ARGUMENTS
from tests.aiguard.litellm.conftest import TOOL_CALL_ID
from tests.aiguard.litellm.conftest import TOOL_NAME
from tests.aiguard.utils import override_ai_guard_config


# All tests use one event loop, because litellm keeps asyncio objects (for example its logging worker) between calls.
pytestmark = pytest.mark.asyncio(loop_scope="module")

OPENAI = dict(model="openai/gpt-4o-mini", api_key="sk-test", api_base="http://openai.test/v1")
AZURE = dict(model="azure/gpt-4o-mini", api_key="sk-test", api_base="http://azure.test", api_version="2024-10-21")
INSTRUCT_MODEL = "text-completion-openai/gpt-3.5-turbo-instruct"
OTHER_PROMPT = "And in Rome?"
MESSAGES = [{"role": "user", "content": PROMPT}]
ANSWER_MESSAGE = {
    "role": "assistant",
    "content": ANSWER,
    "tool_calls": [{"id": TOOL_CALL_ID, "function": {"name": TOOL_NAME, "arguments": TOOL_ARGUMENTS}}],
}
ONE_PROMPT_AND_ONE_ANSWER = [("litellm", "prompt"), ("litellm", "answer")]
HOOK_EVENTS = [f"context.{step}.litellm.call" for step in ("started", "ended")]


def _chat_args(provider: Any, name: str, is_async: bool) -> dict[str, Any]:
    if name == "anthropic":
        client = provider.anthropic_client(is_async)
        model = "anthropic/claude-sonnet-4-5-20250929"
        return dict(model=model, api_key="sk-test", api_base="http://anthropic.test", max_tokens=64, client=client)
    return dict(AZURE if name == "azure" else OPENAI)


async def _completion(is_async: bool, **kwargs: Any) -> Any:
    return await litellm.acompletion(**kwargs) if is_async else litellm.completion(**kwargs)


async def _text_completion(is_async: bool, **kwargs: Any) -> Any:
    return await litellm.atext_completion(**kwargs) if is_async else litellm.text_completion(**kwargs)


async def _call(kind: str, is_async: bool, **kwargs: Any) -> Any:
    if kind == "text":
        return await _text_completion(is_async, prompt=PROMPT, **kwargs)
    return await _completion(is_async, messages=MESSAGES, **kwargs)


async def _read(stream: Any, is_async: bool) -> list[Any]:
    return [chunk async for chunk in stream] if is_async else list(stream)


def _warnings(caplog: Any) -> list[logging.LogRecord]:
    loggers = ("ddtrace.aiguard", "ddtrace.contrib.internal.litellm", "ddtrace.internal.core")
    return [r for r in caplog.records if r.levelno >= logging.WARNING and r.name.startswith(loggers)]


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("name", ["openai", "azure", "anthropic"])
async def test_chat_prompt_and_answer_are_evaluated(
    provider, evaluations, evaluated_messages, evaluation_kwargs, is_async, name
):
    response = await _completion(is_async, messages=MESSAGES, **_chat_args(provider, name, is_async))

    assert response.choices[0].message.tool_calls[0].function.name == TOOL_NAME
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert [kwargs["source"] for kwargs in evaluation_kwargs] == ["auto", "auto"]
    assert evaluated_messages == [MESSAGES, MESSAGES + [ANSWER_MESSAGE]]
    # The provider gets one request. The claim makes the OpenAI listeners skip it.
    assert provider.claimed == [True]
    assert not is_aiguard_context_active()


TOOL_TURN = [
    *MESSAGES,
    {
        "role": "assistant",
        "tool_calls": [
            {"id": TOOL_CALL_ID, "type": "function", "function": ANSWER_MESSAGE["tool_calls"][0]["function"]}
        ],
    },
    {"role": "tool", "tool_call_id": TOOL_CALL_ID, "content": "Sunny"},
]


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    "messages, expected",
    [
        pytest.param(MESSAGES + [{"role": "assistant", "content": "Hi!"}], [("litellm", "answer")], id="assistant"),
        pytest.param(TOOL_TURN, ONE_PROMPT_AND_ONE_ANSWER, id="tool"),
    ],
)
async def test_prompt_is_evaluated_only_when_it_ends_with_a_user_or_tool_message(
    provider, evaluations, is_async, messages, expected
):
    await _completion(is_async, messages=messages, **OPENAI)

    assert evaluations == expected
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    "model, prompt, prompt_messages",
    [
        (OPENAI["model"], PROMPT, [{"role": "user", "content": PROMPT}]),
        (OPENAI["model"], [PROMPT, OTHER_PROMPT], [{"role": "user", "content": p} for p in (PROMPT, OTHER_PROMPT)]),
        (INSTRUCT_MODEL, [PROMPT, OTHER_PROMPT], [{"role": "user", "content": p} for p in (PROMPT, OTHER_PROMPT)]),
        # Token ID prompts have no text, so only the answer is evaluated. litellm 1.78 needs a list of token lists.
        (INSTRUCT_MODEL, [[9906, 1917]], []),
    ],
)
async def test_text_completion_prompt_and_answer_are_evaluated(
    provider, evaluations, evaluated_messages, is_async, model, prompt, prompt_messages
):
    response = await _text_completion(
        is_async, model=model, prompt=prompt, api_key=OPENAI["api_key"], api_base=OPENAI["api_base"]
    )

    answer = [{"role": "assistant", "content": choice.text} for choice in response.choices]
    assert answer and all(message["content"] == ANSWER for message in answer)
    assert evaluations == ([("litellm", "prompt")] if prompt_messages else []) + [("litellm", "answer")]
    assert evaluated_messages[-1] == prompt_messages + answer
    assert provider.claimed == [True]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("drop_params", [False, True])
@pytest.mark.parametrize("target", ["prompt", "answer"])
async def test_block_raises_ai_guard_abort_error(
    provider, evaluations, verdicts, monkeypatch, is_async, drop_params, target
):
    # With drop_params, litellm sends the call again when the OpenAI SDK raises an error with the status 422.
    monkeypatch.setattr(litellm, "drop_params", drop_params)
    verdicts[target] = "DENY"

    with pytest.raises(AIGuardAbortError) as exc_info:
        await _completion(is_async, messages=MESSAGES, **OPENAI)

    # The bare error is not an Exception, so litellm cannot catch it, wrap it or send the call again.
    assert type(exc_info.value) is AIGuardAbortError
    assert not isinstance(exc_info.value, Exception)
    blocked_answer = target == "answer"
    assert len(provider.requests) == int(blocked_answer)
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER[: 1 + int(blocked_answer)]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
async def test_provider_error_releases_the_claim(provider, evaluations, is_async):
    with pytest.raises(litellm.BadRequestError):
        await _completion(is_async, messages=MESSAGES, **{**OPENAI, "model": f"openai/{FAILING_MODEL}"})

    assert evaluations == [("litellm", "prompt")]
    assert provider.claimed == [True]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
async def test_call_under_an_active_claim_is_not_evaluated(provider, evaluations, is_async):
    # For example a LangChain model call or a Strands agent that runs on litellm.
    with aiguard_context():
        await _completion(is_async, messages=MESSAGES, **OPENAI)
        assert is_aiguard_context_active()

    assert evaluations == []
    assert provider.claimed == [True]
    assert not is_aiguard_context_active()


async def test_each_fallback_attempt_is_evaluated(provider, evaluations, evaluated_messages):
    # litellm calls litellm.acompletion again for each fallback model, inside the first call.
    response = await litellm.acompletion(
        messages=MESSAGES, fallbacks=[OPENAI["model"]], **{**OPENAI, "model": f"openai/{FAILING_MODEL}"}
    )

    assert response.choices[0].message.content == ANSWER
    # The first attempt fails at the provider, so only the second one has an answer.
    assert evaluations == [("litellm", "prompt"), ("litellm", "prompt"), ("litellm", "answer")]
    assert evaluated_messages == [MESSAGES, MESSAGES, MESSAGES + [ANSWER_MESSAGE]]
    assert [request["model"] for request in provider.requests] == [FAILING_MODEL, "gpt-4o-mini"]
    assert provider.claimed == [True, True]
    assert not is_aiguard_context_active()


async def test_dict_fallback_messages_are_evaluated(provider, evaluations, evaluated_messages):
    # A dict fallback can replace the messages: the attempt is evaluated with the request it sends.
    other_messages = [{"role": "user", "content": OTHER_PROMPT}]
    fallback = {"model": OPENAI["model"], "messages": other_messages}
    await litellm.acompletion(messages=MESSAGES, fallbacks=[fallback], **{**OPENAI, "model": f"openai/{FAILING_MODEL}"})

    assert evaluated_messages == [MESSAGES, other_messages, other_messages + [ANSWER_MESSAGE]]
    assert provider.requests[-1]["messages"] == other_messages
    assert not is_aiguard_context_active()


async def test_blocked_dict_fallback_prompt_is_not_sent(provider, evaluations, verdicts):
    other_messages = [{"role": "user", "content": OTHER_PROMPT}]
    fallback = {"model": OPENAI["model"], "messages": other_messages}
    verdicts["prompt"] = lambda messages: "DENY" if messages[-1]["content"] == OTHER_PROMPT else "ALLOW"
    with pytest.raises(AIGuardAbortError):
        await litellm.acompletion(
            messages=MESSAGES, fallbacks=[fallback], **{**OPENAI, "model": f"openai/{FAILING_MODEL}"}
        )

    assert [request["model"] for request in provider.requests] == [FAILING_MODEL]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("kind", ["chat", "text"])
async def test_stream_prompt_is_evaluated(provider, evaluations, is_async, kind):
    stream = await _call(kind, is_async, stream=True, **OPENAI)

    assert await _read(stream, is_async)
    # Only the prompt of a streamed call is evaluated.
    assert evaluations == [("litellm", "prompt")]
    assert provider.claimed == [True]
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("kind", ["chat", "text"])
async def test_blocked_stream_prompt_ends_the_span(provider, evaluations, verdicts, test_spans, is_async, kind):
    verdicts["prompt"] = "DENY"

    with pytest.raises(AIGuardAbortError):
        await _call(kind, is_async, stream=True, **OPENAI)

    assert provider.requests == []
    assert evaluations == [("litellm", "prompt")]
    # The tracer writes a trace when all its spans are finished.
    (span,) = [span for span in test_spans.spans if span.name == "litellm.request"]
    assert span.error == 1
    assert "AIGuardAbortError" in span.get_tag("error.type")
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
async def test_blocked_stream_prompt_through_a_router_ends_both_spans(
    provider, evaluations, verdicts, test_spans, is_async
):
    router = Router(model_list=[{"model_name": "chat", "litellm_params": dict(OPENAI)}])
    verdicts["prompt"] = "DENY"

    with pytest.raises(AIGuardAbortError):
        if is_async:
            await router.acompletion(model="chat", messages=MESSAGES, stream=True)
        else:
            router.completion(model="chat", messages=MESSAGES, stream=True)

    assert provider.requests == []
    assert evaluations == [("litellm", "prompt")]
    operation = "acompletion" if is_async else "completion"
    spans = {span.resource: span for span in test_spans.spans if span.name == "litellm.request"}
    assert sorted(spans) == sorted([operation, f"router.{operation}"])
    assert all(span.error == 1 for span in spans.values())


@pytest.mark.parametrize("is_async", [False, True])
async def test_blocked_prompt_through_a_router_is_not_retried(provider, evaluations, verdicts, is_async):
    router = Router(
        model_list=[
            {"model_name": "chat", "litellm_params": dict(OPENAI)},
            {"model_name": "backup", "litellm_params": {**OPENAI, "model": "openai/gpt-4o"}},
        ],
        num_retries=2,
        fallbacks=[{"chat": ["backup"]}],
    )
    verdicts["prompt"] = "DENY"

    with pytest.raises(AIGuardAbortError):
        if is_async:
            await router.acompletion(model="chat", messages=MESSAGES)
        else:
            router.completion(model="chat", messages=MESSAGES)

    assert evaluations == [("litellm", "prompt")]
    assert provider.requests == []
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("error", [OSError("unreachable"), TimeoutError("timed out"), AIGuardClientError("rejected")])
async def test_ai_guard_error_fails_open(provider, evaluations, verdicts, test_spans, caplog, is_async, error):
    verdicts["prompt"] = verdicts["answer"] = error

    with caplog.at_level(logging.DEBUG, logger="ddtrace"):
        response = await _completion(is_async, messages=MESSAGES, **OPENAI)

    assert response.choices[0].message.content == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER
    assert "AI Guard litellm: failed to evaluate the call" in caplog.text
    assert _warnings(caplog) == []
    assert not is_aiguard_context_active()


@pytest.mark.parametrize("is_async", [False, True])
async def test_conversion_error_fails_open(provider, evaluations, test_spans, caplog, is_async):
    with (
        mock.patch(
            "ddtrace.aiguard.integrations._litellm._convert_openai_messages", side_effect=RuntimeError("bad message")
        ),
        caplog.at_level(logging.DEBUG, logger="ddtrace"),
    ):
        response = await _completion(is_async, messages=MESSAGES, **OPENAI)

    assert response.choices[0].message.content == ANSWER
    assert evaluations == []
    # The call is claimed before the conversion, so the OpenAI listeners skip it.
    assert provider.claimed == [True]
    assert "AI Guard litellm: failed to evaluate the call" in caplog.text
    assert _warnings(caplog) == []


@pytest.mark.parametrize("is_async", [False, True])
async def test_monitor_mode_does_not_block(provider, evaluations, verdicts, is_async):
    verdicts["prompt"] = verdicts["answer"] = "DENY"

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        response = await _completion(is_async, messages=MESSAGES, **OPENAI)

    assert response.choices[0].message.content == ANSWER
    assert evaluations == ONE_PROMPT_AND_ONE_ANSWER


@pytest.mark.parametrize("is_async", [False, True])
async def test_proxy_requests_are_left_to_the_guardrail(provider, evaluations, is_async):
    proxy_request = {"url": "http://proxy.test/chat/completions", "method": "POST", "body": {}}

    await _completion(is_async, messages=MESSAGES, proxy_server_request=proxy_request, **OPENAI)

    # The hook takes no claim, so the OpenAI listener evaluates the prompt when litellm uses the create method.
    assert evaluations == ([] if _litellm_sends_openai_chat_with_post() else [("openai", "prompt")])
    assert provider.claimed == [False]
    assert not is_aiguard_context_active()


async def test_listen_registers_the_litellm_events():
    with mock.patch.object(core, "on") as on:
        _listener._litellm_listen(mock.Mock())

    assert sorted(call.args[0] for call in on.call_args_list) == sorted(HOOK_EVENTS)


async def test_switch_off_registers_no_listener(caplog):
    with (
        override_ai_guard_config(dict(_ai_guard_litellm_enabled=False)),
        mock.patch.object(core, "on") as on,
        caplog.at_level(logging.DEBUG, logger="ddtrace"),
    ):
        _listener._litellm_listen(mock.Mock())

    on.assert_not_called()
    assert "DD_AI_GUARD_LITELLM_ENABLED=false" in caplog.text


@pytest.fixture
def hook_switched_off():
    """Register the listeners with DD_AI_GUARD_LITELLM_ENABLED=false, then register them again for the next tests."""
    LiteLLMCallSubscriber.unregister()
    try:
        with override_ai_guard_config(dict(_ai_guard_litellm_enabled=False)):
            _listener._litellm_listen(mock.Mock())
        yield
    finally:
        with override_ai_guard_config(_AI_GUARD_CONFIG):
            _listener._litellm_listen(new_ai_guard_client())


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("name", ["openai", "azure"])
async def test_switch_off_leaves_litellm_calls_to_the_other_integrations(
    provider, evaluations, hook_switched_off, name, is_async
):
    await _completion(is_async, messages=MESSAGES, **_chat_args(provider, name, is_async))

    # The OpenAI listener evaluates the prompt when litellm uses the create method of the OpenAI SDK.
    # From litellm 1.108.1, litellm sends OpenAI chat with the post method, and Azure chat still with create.
    uses_create = name == "azure" or not _litellm_sends_openai_chat_with_post()
    assert evaluations == ([("openai", "prompt")] if uses_create else [])
    assert provider.claimed == [False]
