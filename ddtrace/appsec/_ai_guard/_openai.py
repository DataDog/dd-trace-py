"""AI Guard integration for the OpenAI SDK.

Provides listener functions for ``openai.chat.completions.create.before``
and ``openai.chat.completions.create.after`` events dispatched from the
OpenAI contrib patching layer.

Message conversion:
    OpenAI messages are nearly identical to the AI Guard ``Message``
    TypedDict.  The main work is extracting ``tool_calls`` into the
    ``ToolCall`` / ``Function`` format expected by the API.

Collision handling:
    If a framework integration (LangChain, Strands) has already set
    ``_ai_guard_active`` in the current context, these listeners skip
    evaluation to avoid double-scanning.
"""

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


def _get(obj, key, default=None):
    """Read *key* from a dict or object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _convert_openai_messages(messages):
    """Convert a list of OpenAI chat messages to AI Guard ``Message`` format.

    Handles both plain dicts (user-supplied) and SDK response objects
    (which expose attributes instead of dict keys).
    """
    result = []
    for msg in messages:
        try:
            if msg is None:
                continue
            role = _get(msg, "role", "")
            if not role:
                continue
            ai_msg = Message(role=role)

            content = _get(msg, "content")
            if content is not None:
                ai_msg["content"] = content

            tool_call_id = _get(msg, "tool_call_id")
            if tool_call_id:
                ai_msg["tool_call_id"] = tool_call_id

            tool_calls = _get(msg, "tool_calls")
            if tool_calls:
                ai_msg["tool_calls"] = [
                    ToolCall(
                        id=_get(tc, "id", ""),
                        function=Function(
                            name=_get(_get(tc, "function") or {}, "name", ""),
                            arguments=_get(_get(tc, "function") or {}, "arguments", "{}"),
                        ),
                    )
                    for tc in tool_calls
                ]
            result.append(ai_msg)
        except Exception:
            logger.debug("Failed to convert OpenAI message", exc_info=True)
    return result


def _convert_openai_response(resp):
    """Convert an OpenAI ChatCompletion response to AI Guard ``Message`` list.

    Iterates over ``resp.choices`` and extracts the assistant message
    (content + tool_calls) from each choice.
    """
    result = []
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

            tool_calls = _get(message, "tool_calls")
            if tool_calls:
                ai_msg["tool_calls"] = [
                    ToolCall(
                        id=_get(tc, "id", ""),
                        function=Function(
                            name=_get(_get(tc, "function") or {}, "name", ""),
                            arguments=_get(_get(tc, "function") or {}, "arguments", "{}"),
                        ),
                    )
                    for tc in tool_calls
                ]
            result.append(ai_msg)
        except Exception:
            logger.debug("Failed to convert OpenAI response message", exc_info=True)
    return result


def _openai_chat_completion_before(client, kwargs):
    """Listener for ``openai.chat.completions.create.before``.

    Evaluates the request messages before the LLM call.  Skips when a
    framework-level evaluation is already in progress (collision avoidance).

    Returns ``AIGuardAbortError`` on block (for ``_raising_dispatch`` to
    re-raise), or ``None`` on allow / skip.
    """
    if is_aiguard_context_active():
        return None

    messages = kwargs.get("messages", [])
    if not messages:
        return None

    ai_guard_messages = _convert_openai_messages(messages)
    if not ai_guard_messages:
        return None

    # Only evaluate when the last message is from the user
    if ai_guard_messages[-1].get("role") != "user":
        return None

    try:
        client.evaluate(ai_guard_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        return e
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion request", exc_info=True)
    return None


def _openai_chat_completion_after(client, kwargs, resp):
    """Listener for ``openai.chat.completions.create.after``.

    Evaluates the full conversation (request + response) after the LLM
    returns.  Skips streaming responses (handled separately) and when a
    framework evaluation is already active.

    Returns ``AIGuardAbortError`` on block, or ``None`` on allow / skip.
    """
    if is_aiguard_context_active():
        return None

    request_messages = _convert_openai_messages(kwargs.get("messages", []))
    response_messages = _convert_openai_response(resp)

    if not response_messages:
        return None

    all_messages = request_messages + response_messages

    try:
        client.evaluate(all_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        return e
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion response", exc_info=True)
    return None
