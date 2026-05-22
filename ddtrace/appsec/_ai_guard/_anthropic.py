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
from typing import Any
from typing import Optional

from ddtrace.appsec._ai_guard._common import _get
from ddtrace.appsec._ai_guard._common import wrap_abort_error
from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
from ddtrace.internal import telemetry
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL


logger = ddlogger.get_logger(__name__)


def _report_converter_error(message: str, exc: Optional[BaseException] = None) -> None:
    """Surface converter anomalies via telemetry so they are not silent.

    Both the Python ``logger`` (developer-facing) and the telemetry intake
    (support-facing) get the event; downstream support cycles benefit from
    knowing about a malformed integration boundary rather than the converter
    silently dropping data.
    """
    logger.debug(message, exc_info=exc is not None)
    try:
        if exc is not None:
            telemetry.telemetry_writer.add_error_log(message, exc)
        else:
            telemetry.telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, message)
    except Exception:
        logger.debug("AI Guard anthropic: telemetry log emission failed", exc_info=True)


__all__ = ["_wrap_abort_error"]


def _wrap_abort_error(cause: AIGuardAbortError) -> AIGuardAbortError:
    """Wrap *cause* so it satisfies both ``except AIGuardAbortError`` and
    ``except anthropic.UnprocessableEntityError``.

    Falls back to *cause* unchanged when the Anthropic SDK is not importable;
    catch-by-``AIGuardAbortError`` still works either way.
    """
    exception_class: type[AIGuardAbortError] = AIGuardAbortError
    try:
        # AIDEV-NOTE: import lazily -- ``_anthropic_errors`` pulls in the optional
        # Anthropic SDK at import time. Python's import lock guarantees all
        # concurrent cold imports observe the same class object.
        from ddtrace.appsec._ai_guard._anthropic_errors import AnthropicAIGuardAbortError

        exception_class = AnthropicAIGuardAbortError
    except ImportError:
        logger.warning(
            "AI Guard: failed to import the Anthropic SDK; falling back to bare "
            "AIGuardAbortError. Install ``anthropic`` to get SDK-hierarchy "
            "compatibility (``except anthropic.UnprocessableEntityError``)."
        )

    return wrap_abort_error(cause, exception_class)


def _tool_call_from_block(block: Any) -> ToolCall:
    """Convert a single Anthropic ``tool_use`` content block to an AI Guard ``ToolCall``.

    Anthropic delivers ``input`` as a parsed dict; AI Guard's schema is
    JSON-string-typed, so serialise once via ``json.dumps(..., default=str)``
    to tolerate non-JSON-serializable values (datetimes, custom classes)
    without breaking the converter.
    """
    tool_input = _get(block, "input", {})
    try:
        arguments = json.dumps(tool_input, default=str)
    except Exception as exc:
        _report_converter_error(
            "AI Guard anthropic: failed to serialize tool_use input to JSON; falling back to str()",
            exc,
        )
        raise
    return ToolCall(
        id=_get(block, "id", "") or "",
        function=Function(name=_get(block, "name", "") or "", arguments=arguments),
    )


def _flatten_text_blocks(value: Any) -> str:
    """Join ``text`` fields from a list of Anthropic content blocks (str or list).

    - ``str`` → returned as-is.
    - ``list[block]`` / ``Iterable[block]`` → text fields from
      ``{"type": "text", "text": ...}`` blocks are concatenated.
    - Anything else → ``str(value)``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    # Anthropic declares system as Union[str, Iterable[TextBlockParam]].
    # Materialise generators/tuples so the block-iteration path handles them;
    # bytes/bytearray are excluded (Iterable[int], not valid here).
    if not isinstance(value, (list, bytes, bytearray)):
        try:
            value = list(value)
        except TypeError:
            pass
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


def _convert_anthropic_messages(system: Any, messages: Any) -> list[Message]:
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
    try:
        if system:
            system_text = _flatten_text_blocks(system)
            if system_text:
                result.append(Message(role="system", content=system_text))

        if not messages:
            return result

        for msg in messages:
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

            # MessageParam.content is typed Iterable[...] not list[...].
            # Materialise generators/tuples; without this a generator stringifies
            # to "<generator object ...>" and bypasses AI Guard evaluation while
            # the SDK still sends the real blocks. Write back so the SDK does not
            # receive an exhausted generator. bytes/bytearray/dict are not valid
            # content shapes — leave them for the stringify fallback below.
            if not isinstance(content, (list, bytes, bytearray, dict)):
                try:
                    content = list(content)
                except TypeError:
                    pass
                else:
                    try:
                        msg["content"] = content
                    except (TypeError, AttributeError):
                        pass

            if not isinstance(content, list):
                # Unknown shape — emit a best-effort message so AI Guard still
                # sees the role/turn boundary; stringify whatever was provided.
                # AIDEV-NOTE: this branch indicates an integration mismatch
                # (bytes, bytearray, dict, or other non-iterable); telemetry-log
                # the offending type so support can spot the regression.
                _report_converter_error(
                    "AI Guard anthropic: unexpected 'content' type %r on role=%r; "
                    "stringifying as best-effort fallback" % (type(content).__name__, role)
                )
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


def _convert_anthropic_response(resp: Any) -> list[Message]:
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


def _anthropic_messages_create_before(client: AIGuardClient, kwargs: dict[str, Any]) -> None:
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
    if len(ai_guard_messages) == 0:
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
    try:
        client.evaluate(ai_guard_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate Anthropic messages request", exc_info=True)
    return None


def _anthropic_messages_create_after(client: AIGuardClient, kwargs: dict[str, Any], resp: Any) -> None:
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

    try:
        client.evaluate(all_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate Anthropic messages response", exc_info=True)
    return None
