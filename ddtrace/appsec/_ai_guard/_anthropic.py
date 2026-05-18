"""AI Guard integration for the Anthropic SDK.

Provides listener functions for ``anthropic.messages.create.before`` and
``anthropic.messages.create.after`` events dispatched from the Anthropic
contrib patching layer. Covers sync + async, stable + Beta,
non-streaming + streaming. For streaming responses only the
``.before`` event fires (request inputs are evaluated before the SDK call);
``.after`` is gated at the contrib dispatch site on
``not is_streaming_operation(resp)``.
"""

import json
import threading

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec._ai_guard._openai import _get
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


# AIDEV-NOTE: The compound ``AnthropicAIGuardAbortError`` class is built lazily
# on first block. ``_anthropic.py`` is imported unconditionally by the AI Guard
# listener (see ``_listener.py``), but the Anthropic SDK is an optional runtime
# dependency — eagerly importing ``anthropic`` here would break AI Guard
# initialization in environments that only use a different provider.
_anthropic_abort_error_cls = None

# The lazy build uses double-checked locking: the unsynchronised check-then-act
# pattern would let two threads simultaneously enter the build branch and each
# create their own class, so callers that captured ``type(err)`` from one block
# would silently stop matching errors from a concurrent block.
_anthropic_abort_error_cls_lock = threading.Lock()


def _get_anthropic_abort_error_cls():
    """Return ``AnthropicAIGuardAbortError`` (cached), or ``None`` if anthropic is not importable.

    The class inherits from ``anthropic.UnprocessableEntityError`` (status 422
    — the "policy rejection" semantics used by AI Gateway) and from
    ``AIGuardAbortError`` so existing Datadog-specific handlers keep working.
    """
    global _anthropic_abort_error_cls
    # Fast path: already cached, no lock needed (assignment to a module
    # attribute is atomic under the GIL).
    if _anthropic_abort_error_cls is not None:
        return _anthropic_abort_error_cls

    with _anthropic_abort_error_cls_lock:
        # Re-check inside the lock: another thread may have built the class
        # while we were blocked on acquisition.
        if _anthropic_abort_error_cls is not None:
            return _anthropic_abort_error_cls

        try:
            import anthropic
        except ImportError:
            return None

        class AnthropicAIGuardAbortError(anthropic.UnprocessableEntityError, AIGuardAbortError):
            """AI Guard abort error compatible with the Anthropic SDK error hierarchy.

            Catchable as either ``anthropic.APIError`` /
            ``anthropic.UnprocessableEntityError`` (idiomatic Anthropic error
            handling, no retry on 422) or ``AIGuardAbortError``
            (Datadog-specific, exposes ``action`` / ``reason``).

            AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
            ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)``
            so a generic ``except Exception:`` does NOT catch it (by design — a
            block decision must not be silently swallowed). However,
            ``AnthropicAIGuardAbortError`` *also* inherits from
            ``anthropic.UnprocessableEntityError``, which is ``Exception``-derived,
            so via MRO this subclass IS catchable by ``except Exception:``.
            That asymmetry is intentional: the Anthropic contrib must keep
            ``except anthropic.APIError:`` blocks working unchanged for users
            migrating from non-AI-Guard error handling. Code that wants
            uniform block detection across providers should branch on
            ``isinstance(e, AIGuardAbortError)``.
            """

            def __init__(self, action, reason, tags=None, sds=None, tag_probs=None):
                self.action = action
                self.reason = reason
                self.tags = tags
                self.sds = sds or []
                self.tag_probs = tag_probs

                message = f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')"
                # AIDEV-NOTE: We can't call ``anthropic.UnprocessableEntityError.__init__``
                # here — its MRO super() chain ends up at ``AIGuardAbortError.__init__``
                # (which requires ``(action, reason)``), not ``Exception.__init__``,
                # so the ``super().__init__(message)`` deep in ``APIError`` raises
                # ``TypeError: missing 'reason'``. Initialize the Anthropic-side
                # attributes directly instead. The block originates from AI Guard,
                # not an Anthropic HTTP call, so ``response`` / ``request`` /
                # ``request_id`` are ``None``; only ``status_code`` (422) and
                # ``body`` (the AI Guard decision payload) carry semantic meaning.
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

        _anthropic_abort_error_cls = AnthropicAIGuardAbortError
        return _anthropic_abort_error_cls


def _wrap_abort_error(cause):
    """Wrap an ``AIGuardAbortError`` into the Anthropic-compatible variant.

    Falls back to returning the original ``cause`` when the Anthropic SDK is not
    importable — the listener still surfaces a block, just without Anthropic
    exception-hierarchy compatibility.
    """
    cls = _get_anthropic_abort_error_cls()
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


def _flatten_text_blocks(value):
    """Join ``text`` fields from a list of Anthropic content blocks (str or list).

    - ``str`` → returned as-is.
    - ``list[block]`` → text fields from ``{"type": "text", "text": ...}``
      blocks are concatenated.
    - Anything else → ``str(value)``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            block_type = _get(block, "type", "")
            if block_type == "text":
                text = _get(block, "text", "")
                if text:
                    parts.append(text)
        return "".join(parts)
    return str(value)


def _convert_anthropic_messages(system, messages):
    """Convert Anthropic messages + ``system`` prompt to AI Guard ``Message`` format.

    Anthropic exposes ``system`` as a separate top-level kwarg (``str`` or a
    list of ``{"type": "text", ...}`` blocks). It is prepended as a synthetic
    ``Message(role="system")`` so AI Guard sees the full conversation.

    Handles both dict-shaped (user-supplied) and SDK-object-shaped (response)
    entries via :func:`_get`.

    Content blocks:
      - ``text`` → accumulated into the message's ``content`` string.
      - ``tool_use`` (assistant) → translated to ``ToolCall(id, function=...)``;
        ``input`` is serialised via ``json.dumps(..., default=str)`` to tolerate
        non-JSON-serializable values without breaking the converter.
      - ``tool_result`` (user) → emitted as a separate ``Message(role="tool",
        tool_call_id=...)``. Nested ``content`` (str or list of text blocks) is
        flattened to a string.
      - ``image`` / ``document`` / ``thinking`` → dropped (AI Guard evaluates
        text/tool semantics only).
    """
    result = []

    if system:
        system_text = _flatten_text_blocks(system)
        if system_text:
            result.append(Message(role="system", content=system_text))

    if not messages:
        return result

    for msg in messages:
        try:
            if msg is None:
                continue
            role = _get(msg, "role", "")
            if not role:
                continue
            content = _get(msg, "content")

            if isinstance(content, str):
                ai_msg = Message(role=role, content=content)
                result.append(ai_msg)
                continue

            if not isinstance(content, list):
                # Unknown shape — emit a best-effort message so AI Guard still
                # sees the role/turn boundary; stringify whatever was provided.
                ai_msg = Message(role=role)
                if content is not None:
                    ai_msg["content"] = str(content)
                result.append(ai_msg)
                continue

            text_parts = []
            tool_calls_out = []
            tool_result_msgs = []
            for block in content:
                block_type = _get(block, "type", "")
                if block_type == "text":
                    text = _get(block, "text", "")
                    if text:
                        text_parts.append(text)
                elif block_type == "tool_use":
                    tool_id = _get(block, "id", "") or ""
                    tool_name = _get(block, "name", "") or ""
                    tool_input = _get(block, "input", {})
                    try:
                        arguments = json.dumps(tool_input, default=str)
                    except Exception:
                        arguments = str(tool_input)
                    tool_calls_out.append(
                        ToolCall(
                            id=tool_id,
                            function=Function(name=tool_name, arguments=arguments),
                        )
                    )
                elif block_type == "tool_result":
                    tool_use_id = _get(block, "tool_use_id", "") or ""
                    tool_content = _flatten_text_blocks(_get(block, "content"))
                    tool_msg = Message(role="tool", tool_call_id=tool_use_id)
                    if tool_content:
                        tool_msg["content"] = tool_content
                    tool_result_msgs.append(tool_msg)
                else:
                    logger.debug("AI Guard anthropic: dropping unsupported content block type %r", block_type)

            if text_parts or tool_calls_out or not tool_result_msgs:
                ai_msg = Message(role=role)
                if text_parts:
                    ai_msg["content"] = "".join(text_parts)
                if tool_calls_out:
                    ai_msg["tool_calls"] = tool_calls_out
                result.append(ai_msg)

            result.extend(tool_result_msgs)
        except Exception:
            logger.debug("Failed to convert Anthropic message", exc_info=True)
    return result


def _convert_anthropic_response(resp):
    """Convert a non-streaming Anthropic ``Message`` response to AI Guard ``Message`` list.

    Anthropic responses expose ``role`` (always ``"assistant"``) and a
    ``content`` list of ``text`` and/or ``tool_use`` blocks. Emits a single
    assistant ``Message`` carrying the concatenated text and any tool_calls.
    Returns ``[]`` when nothing convertible was produced.
    """
    if resp is None:
        return []
    try:
        role = _get(resp, "role", "assistant") or "assistant"
        content = _get(resp, "content")
        if not isinstance(content, list):
            return []

        text_parts = []
        tool_calls_out = []
        for block in content:
            block_type = _get(block, "type", "")
            if block_type == "text":
                text = _get(block, "text", "")
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_id = _get(block, "id", "") or ""
                tool_name = _get(block, "name", "") or ""
                tool_input = _get(block, "input", {})
                try:
                    arguments = json.dumps(tool_input, default=str)
                except Exception:
                    arguments = str(tool_input)
                tool_calls_out.append(
                    ToolCall(
                        id=tool_id,
                        function=Function(name=tool_name, arguments=arguments),
                    )
                )

        if not text_parts and not tool_calls_out:
            return []

        ai_msg = Message(role=role)
        if text_parts:
            ai_msg["content"] = "".join(text_parts)
        if tool_calls_out:
            ai_msg["tool_calls"] = tool_calls_out
        return [ai_msg]
    except Exception:
        logger.debug("Failed to convert Anthropic response", exc_info=True)
        return []


def _anthropic_messages_create_before(client, kwargs):
    """Listener for ``anthropic.messages.create.before``.

    Evaluates the request inputs before the Anthropic SDK is called. Fires for
    streaming and non-streaming requests alike. Skipped when a framework
    integration (LangChain, Strands) already has an active AI Guard context.
    """
    if is_aiguard_context_active():
        logger.debug("AI Guard anthropic before-hook skipped: framework context active (e.g. LangChain)")
        return None

    messages = kwargs.get("messages")
    if messages is None:
        logger.debug("AI Guard anthropic before-hook skipped: kwargs has no 'messages' key")
        return None
    if not isinstance(messages, (list, tuple)):
        try:
            messages = list(messages)
        except TypeError:
            logger.debug("AI Guard anthropic before-hook skipped: messages is not iterable")
            return None
        kwargs["messages"] = messages
    if not messages:
        logger.debug("AI Guard anthropic before-hook skipped: messages is empty")
        return None

    system = kwargs.get("system")
    ai_guard_messages = _convert_anthropic_messages(system, messages)
    if not ai_guard_messages:
        logger.debug("AI Guard anthropic before-hook skipped: no convertible messages")
        return None

    last_role = ai_guard_messages[-1].get("role")
    if last_role not in ("user", "tool"):
        logger.debug(
            "AI Guard anthropic before-hook skipped: last message role is %r, expected 'user' or 'tool'",
            last_role,
        )
        return None

    logger.debug("AI Guard anthropic before-hook evaluating %d message(s)", len(ai_guard_messages))
    try:
        client.evaluate(ai_guard_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate Anthropic messages request", exc_info=True)
    return None


def _anthropic_messages_create_after(client, kwargs, resp):
    """Listener for ``anthropic.messages.create.after``.

    Evaluates the full conversation (request + response) after the model
    returns. Only fires for non-streaming responses (the contrib dispatch site
    gates on ``not is_streaming_operation(resp)``). Skipped when a framework
    integration already has an active AI Guard context.

    On block: raises an ``AnthropicAIGuardAbortError`` (or plain
    ``AIGuardAbortError`` when the Anthropic SDK is not importable). Allow /
    skip paths return ``None``.
    """
    if is_aiguard_context_active():
        logger.debug("AI Guard anthropic after-hook skipped: framework context active (e.g. LangChain)")
        return None

    request_messages = _convert_anthropic_messages(kwargs.get("system"), kwargs.get("messages", []))
    response_messages = _convert_anthropic_response(resp)
    if not response_messages:
        logger.debug("AI Guard anthropic after-hook skipped: no convertible response messages")
        return None

    all_messages = request_messages + response_messages

    try:
        client.evaluate(all_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate Anthropic messages response", exc_info=True)
    return None
