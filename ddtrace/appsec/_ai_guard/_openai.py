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

Block error type:
    When AI Guard blocks a request, the listeners raise
    ``OpenAIAIGuardAbortError`` — a subclass of both
    ``openai.UnprocessableEntityError`` (HTTP 422) and ``AIGuardAbortError``.
    This lets callers handle a block via either standard OpenAI error
    handling (``except openai.APIError``) or the Datadog-specific exception
    (``except AIGuardAbortError``).
"""

from collections import deque

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


# AIDEV-NOTE: The compound ``OpenAIAIGuardAbortError`` class is built lazily on
# first block. ``_openai.py`` is imported unconditionally by the AI Guard
# listener (see ``_listener.py``), but the OpenAI SDK is an optional runtime
# dependency — eagerly importing ``openai`` here would break AI Guard
# initialization in environments that only use a different provider.
_openai_abort_error_cls = None


def _get_openai_abort_error_cls():
    """Return ``OpenAIAIGuardAbortError`` (cached), or ``None`` if openai is not importable.

    The class inherits from ``openai.UnprocessableEntityError`` (status 422 —
    the "policy rejection" semantics used by AI Gateway) and from
    ``AIGuardAbortError`` so existing Datadog-specific handlers keep working.
    """
    global _openai_abort_error_cls
    if _openai_abort_error_cls is not None:
        return _openai_abort_error_cls

    try:
        import openai
    except ImportError:
        return None

    class OpenAIAIGuardAbortError(openai.UnprocessableEntityError, AIGuardAbortError):
        """AI Guard abort error compatible with the OpenAI SDK error hierarchy.

        Catchable as either ``openai.APIError`` / ``openai.UnprocessableEntityError``
        (idiomatic OpenAI error handling, no retry on 422) or
        ``AIGuardAbortError`` (Datadog-specific, exposes ``action`` / ``reason``).
        """

        def __init__(self, action, reason, tags=None, sds=None, tag_probs=None):
            self.action = action
            self.reason = reason
            self.tags = tags
            self.sds = sds or []
            self.tag_probs = tag_probs

            message = f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')"
            # AIDEV-NOTE: We can't call ``openai.UnprocessableEntityError.__init__``
            # here — its MRO super() chain ends up at ``AIGuardAbortError.__init__``
            # (which requires ``(action, reason)``), not ``Exception.__init__``,
            # so the ``super().__init__(message)`` deep in ``APIError`` raises
            # ``TypeError: missing 'reason'``. Initialize the OpenAI-side
            # attributes directly instead. The block originates from AI Guard,
            # not an OpenAI HTTP call, so ``response`` / ``request`` / ``request_id``
            # are ``None``; only ``status_code`` (422) and ``body`` (the AI Guard
            # decision payload) carry semantic meaning.
            self.message = message
            self.request = None
            self.body = {"action": action, "reason": reason, "source": "datadog_ai_guard"}
            self.code = "ai_guard_block"
            self.param = None
            self.type = "ai_guard_abort"
            self.response = None
            self.status_code = 422
            self.request_id = None
            Exception.__init__(self, message)

    _openai_abort_error_cls = OpenAIAIGuardAbortError
    return _openai_abort_error_cls


def _wrap_abort_error(cause):
    """Wrap an ``AIGuardAbortError`` into the OpenAI-compatible variant.

    Falls back to returning the original ``cause`` when the OpenAI SDK is not
    importable — the listener still surfaces a block, just without OpenAI
    exception-hierarchy compatibility.
    """
    cls = _get_openai_abort_error_cls()
    if cls is None:
        return cause
    wrapped = cls(
        action=cause.action,
        reason=cause.reason,
        tags=cause.tags,
        sds=cause.sds,
        tag_probs=cause.tag_probs,
    )
    wrapped.__cause__ = cause
    return wrapped


def _get(obj, key, default=None):
    """Read *key* from a dict or object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _convert_openai_messages(messages):
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
    result = []
    pending_fc_ids: deque = deque()
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
                tool_calls_out = []
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


def _tool_call_from(tc):
    """Build a ``ToolCall`` from an OpenAI tool_call (dict or SDK object).

    Hoists ``_get(tc, "function")`` to a single lookup — this runs once per
    tool call on the OpenAI hot path.
    """
    fn = _get(tc, "function") or {}
    return ToolCall(
        id=_get(tc, "id", ""),
        function=Function(
            name=_get(fn, "name", ""),
            arguments=_get(fn, "arguments", "{}"),
        ),
    )


def _convert_openai_response(resp):
    """Convert an OpenAI ChatCompletion response to AI Guard ``Message`` list.

    Iterates over ``resp.choices`` and extracts the assistant message
    (content + tool_calls) from each choice. Legacy ``function_call``
    responses are translated to a synthetic ``ToolCall`` (see
    ``_convert_openai_messages`` for rationale); response-side uses
    ``fc_<id():x>`` since each choice is independent and there is no
    pairing within a single response.
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

            tool_calls_out = []
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


def _openai_chat_completion_before(client, kwargs):
    """Listener for ``openai.chat.completions.create.before``.

    Evaluates the request messages before the LLM call.  Skips when a
    framework-level evaluation is already in progress (collision avoidance).

    Returns ``OpenAIAIGuardAbortError`` (or plain ``AIGuardAbortError`` if
    openai is not importable) on block — for ``_raising_dispatch`` to re-raise
    — or ``None`` on allow / skip.
    """
    if is_aiguard_context_active():
        return None

    messages = kwargs.get("messages", [])
    if not messages:
        return None

    ai_guard_messages = _convert_openai_messages(messages)
    if not ai_guard_messages:
        return None

    # AIDEV-NOTE: Before-model evaluation fires when the last message is
    # role="user" (new user prompt) or role="tool" (tool result feeding back
    # into the next model call). Per the AI Guard spec ("Anatomy of an AI
    # Guard evaluation"), tool results are evaluated either "after tool"
    # (framework hook) or at "next before model" — provider SDKs have no
    # after-tool hook, so this is the prevention window that catches indirect
    # prompt injection in tool output before the LLM processes it.
    # Framework collisions (LangChain/Strands wrapping the loop) are already
    # prevented by the is_aiguard_context_active() check above.
    if ai_guard_messages[-1].get("role") not in ("user", "tool"):
        return None

    try:
        client.evaluate(ai_guard_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        return _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion request", exc_info=True)
    return None


def _openai_chat_completion_after(client, kwargs, resp):
    """Listener for ``openai.chat.completions.create.after``.

    Evaluates the full conversation (request + response) after the LLM
    returns.  Skips streaming responses (handled separately) and when a
    framework evaluation is already active.

    Returns ``OpenAIAIGuardAbortError`` (or plain ``AIGuardAbortError`` if
    openai is not importable) on block, or ``None`` on allow / skip.
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
        return _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion response", exc_info=True)
    return None
