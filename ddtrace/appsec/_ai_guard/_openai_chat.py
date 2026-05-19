"""AI Guard integration for the OpenAI Chat Completions API.

Provides listener functions for ``openai.chat.completions.create.before`` and
``openai.chat.completions.create.after`` events dispatched from the OpenAI
contrib patching layer.
"""

from collections import deque
from typing import Any

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec._ai_guard._openai import _get
from ddtrace.appsec._ai_guard._openai import _wrap_abort_error
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


def _convert_openai_messages(messages: Any) -> list[Message]:
    """Convert a list of OpenAI chat messages to AI Guard ``Message`` format.

    Handles both plain dicts (user-supplied) and SDK response objects
    (which expose attributes instead of dict keys).

    Legacy translation: OpenAI's deprecated single-call API still ships in
    ``openai>=1.102.0`` (our minimum supported version). Assistant
    ``function_call`` and ``role="function"`` response messages are
    translated to AI Guard's canonical ``tool_calls`` / ``role="tool"``
    shape — the ``Message`` schema in ``_api_client.py`` only models
    ``tool_calls``/``tool_call_id``, so without translation the function
    name and arguments would be invisible to the evaluator. Pairing uses a
    FIFO of synthetic ids ``fc_N`` so the matching ``role="function"``
    result correlates with the assistant turn that issued it. Mirrors the
    canonical pattern in ``ai_guard/integrations/litellm.py``.
    """
    result: list[Message] = []
    pending_fc_ids: deque[str] = deque()
    fc_counter = 0
    for msg in messages:
        try:
            if msg is None:
                continue
            role = _get(msg, "role", "")
            if not role:
                continue

            if role == "function":
                ai_msg = Message(role="tool")
                ai_msg["tool_call_id"] = pending_fc_ids.popleft() if pending_fc_ids else ""
            else:
                ai_msg = Message(role=role)
                tool_call_id = _get(msg, "tool_call_id")
                if tool_call_id:
                    ai_msg["tool_call_id"] = tool_call_id

            content = _get(msg, "content")
            if content is not None:
                ai_msg["content"] = content

            if role == "assistant":
                tool_calls_out: list[ToolCall] = []
                tool_calls = _get(msg, "tool_calls")
                if tool_calls:
                    tool_calls_out.extend(_tool_call_from(tc) for tc in tool_calls)
                function_call = _get(msg, "function_call")
                if function_call:
                    synthetic_id = f"fc_{fc_counter}"
                    fc_counter += 1
                    pending_fc_ids.append(synthetic_id)
                    tool_calls_out.append(
                        ToolCall(
                            id=synthetic_id,
                            function=Function(
                                name=_get(function_call, "name", "") or "",
                                arguments=_get(function_call, "arguments", "{}") or "{}",
                            ),
                        )
                    )
                if tool_calls_out:
                    ai_msg["tool_calls"] = tool_calls_out
            else:
                tool_calls = _get(msg, "tool_calls")
                if tool_calls:
                    ai_msg["tool_calls"] = [_tool_call_from(tc) for tc in tool_calls]
            result.append(ai_msg)
        except Exception:
            logger.debug("Failed to convert OpenAI message", exc_info=True)
    return result


def _tool_call_from(tc: Any) -> ToolCall:
    """Build a ``ToolCall`` from an OpenAI tool_call (dict or SDK object)."""
    fn = _get(tc, "function") or {}
    return ToolCall(
        id=_get(tc, "id", ""),
        function=Function(
            name=_get(fn, "name", ""),
            arguments=_get(fn, "arguments", "{}"),
        ),
    )


def _convert_openai_response(resp: Any) -> list[Message]:
    """Convert an OpenAI ChatCompletion response to AI Guard ``Message`` list."""
    result: list[Message] = []
    choices = _get(resp, "choices") or []
    for choice in choices:
        try:
            message = _get(choice, "message")
            if message is None:
                continue
            role = _get(message, "role", "assistant")
            ai_msg = Message(role=role)

            content = _get(message, "content")
            if content is not None:
                ai_msg["content"] = content

            tool_calls_out: list[ToolCall] = []
            tool_calls = _get(message, "tool_calls")
            if tool_calls:
                tool_calls_out.extend(_tool_call_from(tc) for tc in tool_calls)
            function_call = _get(message, "function_call")
            if function_call:
                tool_calls_out.append(
                    ToolCall(
                        id=f"fc_{id(function_call):x}",
                        function=Function(
                            name=_get(function_call, "name", "") or "",
                            arguments=_get(function_call, "arguments", "{}") or "{}",
                        ),
                    )
                )
            if tool_calls_out:
                ai_msg["tool_calls"] = tool_calls_out
            result.append(ai_msg)
        except Exception:
            logger.debug("Failed to convert OpenAI response message", exc_info=True)
    return result


def _openai_chat_completion_before(client: AIGuardClient, kwargs: dict[str, Any]) -> None:
    """Listener for ``openai.chat.completions.create.before``."""
    if is_aiguard_context_active():
        logger.debug("AI Guard openai before-hook skipped: framework context active (e.g. Strands plugin)")
        return None

    messages = kwargs.get("messages")
    if messages is None:
        logger.debug("AI Guard openai before-hook skipped: kwargs has no 'messages' key")
        return None

    if not isinstance(messages, (list, tuple)):
        messages = list(messages)
        kwargs["messages"] = messages
    if not messages:
        logger.debug("AI Guard openai before-hook skipped: messages is empty")
        return None

    ai_guard_messages = _convert_openai_messages(messages)
    if not ai_guard_messages:
        logger.debug("AI Guard openai before-hook skipped: no convertible messages")
        return None

    if ai_guard_messages[-1].get("role") not in ("user", "tool"):
        logger.debug(
            "AI Guard openai before-hook skipped: last message role is %r, expected 'user' or 'tool'",
            ai_guard_messages[-1].get("role"),
        )
        return None

    logger.debug("AI Guard openai before-hook evaluating %d message(s)", len(ai_guard_messages))
    try:
        client.evaluate(ai_guard_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion request", exc_info=True)
    return None


def _openai_chat_completion_after(client: AIGuardClient, kwargs: dict[str, Any], resp: Any) -> None:
    """Listener for ``openai.chat.completions.create.after``.

    Evaluates the full conversation (request + response) after the LLM
    returns.  Skips streaming responses (handled separately) and when a
    framework evaluation is already active.

    On block: raises an ``OpenAIAIGuardAbortError`` (or plain
    ``AIGuardAbortError`` when the OpenAI SDK is not importable).  Allow /
    skip paths return ``None``.
    """
    if is_aiguard_context_active():
        logger.debug("AI Guard openai after-hook skipped: framework context active (e.g. Strands plugin)")
        return None

    request_messages = _convert_openai_messages(kwargs.get("messages", []))
    response_messages = _convert_openai_response(resp)

    if not response_messages:
        logger.debug("AI Guard openai after-hook skipped: no convertible response messages")
        return None

    all_messages = request_messages + response_messages

    try:
        client.evaluate(all_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion response", exc_info=True)
    return None
