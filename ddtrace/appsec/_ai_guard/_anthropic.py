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

from ddtrace.appsec._ai_guard._common import _get
from ddtrace.appsec._ai_guard._common import build_compound_abort_error_cls
from ddtrace.appsec._ai_guard._common import evaluate_messages
from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import ToolCall
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)


def _get_anthropic_abort_error_cls():
    """Return the Anthropic-compatible compound abort error class, or ``None`` if anthropic is not importable."""
    try:
        import anthropic
    except ImportError:
        return None
    return build_compound_abort_error_cls("Anthropic", anthropic.UnprocessableEntityError)


def _tool_call_from_block(block):
    """Convert a single Anthropic ``tool_use`` content block to an AI Guard ``ToolCall``.

    Anthropic delivers ``input`` as a parsed dict; AI Guard's schema is
    JSON-string-typed, so serialise once via ``json.dumps(..., default=str)``
    to tolerate non-JSON-serializable values (datetimes, custom classes)
    without breaking the converter.
    """
    tool_input = _get(block, "input", {})
    try:
        arguments = json.dumps(tool_input, default=str)
    except Exception:
        arguments = str(tool_input)
    return ToolCall(
        id=_get(block, "id", "") or "",
        function=Function(name=_get(block, "name", "") or "", arguments=arguments),
    )


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
                    tool_calls_out.append(_tool_call_from_block(block))
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
                tool_calls_out.append(_tool_call_from_block(block))

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

    # AIDEV-NOTE: Anthropic supports final assistant turns as response prefill
    # input; unlike OpenAI, those are still pre-model request content and must
    # be evaluated before calling the SDK.
    last_role = ai_guard_messages[-1].get("role")
    if last_role not in ("user", "tool", "assistant"):
        logger.debug(
            "AI Guard anthropic before-hook skipped: last message role is %r, expected 'user', 'tool', or 'assistant'",
            last_role,
        )
        return None

    logger.debug("AI Guard anthropic before-hook evaluating %d message(s)", len(ai_guard_messages))
    evaluate_messages(client, ai_guard_messages, _get_anthropic_abort_error_cls(), "Anthropic messages request")
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

    # Convert response first: if the model produced nothing convertible
    # (e.g. an empty content list), skip request-side conversion entirely.
    response_messages = _convert_anthropic_response(resp)
    if not response_messages:
        logger.debug("AI Guard anthropic after-hook skipped: no convertible response messages")
        return None

    request_messages = _convert_anthropic_messages(kwargs.get("system"), kwargs.get("messages", []))
    all_messages = request_messages + response_messages
    evaluate_messages(client, all_messages, _get_anthropic_abort_error_cls(), "Anthropic messages response")
    return None
