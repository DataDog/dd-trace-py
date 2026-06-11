"""AI Guard integration for the Anthropic SDK.

Provides listener functions for ``anthropic.messages.create.before`` and
``anthropic.messages.create.after`` events dispatched from the Anthropic
contrib patching layer. Covers sync + async, stable + Beta,
non-streaming + streaming. For streaming responses only the
``.before`` event fires (request inputs are evaluated before the SDK call);
``.after`` is gated at the contrib dispatch site on
``not is_streaming_operation(resp)``.

The converter accepts every Anthropic Messages-API content block shape we
expect to encounter at the SDK boundary; the mapping mirrors the dd-source
``format_messages`` reference (``domains/appsec/shared/libs/py/aiguard``).
Shapes outside the recognised set are telemetry-logged and replaced with a
placeholder (``[GENERATOR]`` for single-use iterators, ``str(content)`` for
the rest) so AI Guard still scans the request rather than silently bypassing
it, and support can spot integration drift.
"""

from collections.abc import Iterator
import json
from typing import Any
from typing import NamedTuple
from typing import Optional
from typing import Sequence
from typing import Union

from ddtrace.appsec._ai_guard._common import _get
from ddtrace.appsec._ai_guard._common import wrap_abort_error
from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import ContentPart
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import ImageURL
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


# ---------------------------------------------------------------------------
# Content-block parsing
# ---------------------------------------------------------------------------


class _ParsedBlocks(NamedTuple):
    """Result of parsing a list of Anthropic content blocks.

    ``pre_tool_parts`` / ``post_tool_parts`` split text + image content around
    server-tool boundaries so an assistant turn that interleaves
    ``server_tool_use`` blocks, server tool results, and synthesis text can be
    emitted as three distinct AI Guard messages (tool_call -> tool_result ->
    synthesis), matching the dd-source reference.
    """

    pre_tool_parts: list[ContentPart]
    post_tool_parts: list[ContentPart]
    tool_calls: list[ToolCall]
    tool_results: list[Message]
    server_tool_results: list[Message]


def _reduce_parts(parts: list[ContentPart]) -> Union[str, list[ContentPart]]:
    """Collapse a content-part list to a plain string when every part is text.

    Keeps AI Guard payloads compact for the common text-only case while still
    supporting multi-modal ``list[ContentPart]`` shapes (image + text) when an
    image is present.
    """
    if not parts:
        return ""
    if all(p.get("type") == "text" for p in parts):
        return "".join(p.get("text", "") or "" for p in parts)
    return parts


def _format_image_block(block: Any) -> Optional[ContentPart]:
    """Convert an Anthropic ``image`` block to an ``image_url`` ContentPart.

    Supports both base64 (``source.type == "base64"``) and URL
    (``source.type == "url"``) sources. Returns ``None`` when the source is
    missing required fields -- silently dropping a partial image avoids
    surfacing garbage URLs to AI Guard's classifier.
    """
    source = _get(block, "source")
    if not source:
        return None
    source_type = _get(source, "type", "") or ""
    if source_type == "url":
        url = _get(source, "url", "") or ""
    else:
        media_type = _get(source, "media_type", "") or ""
        data = _get(source, "data", "") or ""
        url = f"data:{media_type};base64,{data}" if media_type and data else ""
    if not url:
        return None
    return ContentPart(type="image_url", image_url=ImageURL(url=url))


def _tool_call_from_block(block: Any) -> ToolCall:
    """Convert a single Anthropic ``tool_use`` / ``server_tool_use`` block.

    Anthropic delivers ``input`` as a parsed dict; AI Guard's schema is
    JSON-string-typed, so serialise once via ``json.dumps(..., default=str)``
    to tolerate non-JSON-serializable values (datetimes, custom classes)
    without breaking the converter. A serialisation failure is telemetry-logged
    and re-raised: a converter that drops tool calls silently would let
    AI Guard score a request that is missing the model's actual intent.
    """
    tool_input = _get(block, "input", {})
    try:
        arguments = json.dumps(tool_input, default=str)
    except Exception as exc:
        _report_converter_error(
            "AI Guard anthropic: failed to serialize tool_use input to JSON",
            exc,
        )
        raise
    return ToolCall(
        id=_get(block, "id", "") or "",
        function=Function(name=_get(block, "name", "") or "", arguments=arguments),
    )


def _format_tool_result_block(block: Any) -> list[Message]:
    """Convert a user-side ``tool_result`` block to AI Guard tool message(s).

    Anthropic ``tool_result.content`` is either a string or a list of
    ``text`` / ``image`` blocks. When images are present, the message uses
    a ``list[ContentPart]`` content; otherwise the parts collapse to a plain
    string for compactness. ``is_error`` is intentionally not mapped --
    AI Guard scans content regardless of error status.
    """
    tool_use_id = _get(block, "tool_use_id", "") or ""
    content = _get(block, "content", "")
    msg = Message(role="tool", tool_call_id=tool_use_id)

    if isinstance(content, str):
        if content:
            msg["content"] = content
        return [msg]

    # MessageParam tool_result.content is typed Iterable[...]; materialise
    # generators / tuples so the block-iteration path handles them. dict and
    # bytes/bytearray are not valid container shapes here.
    if not isinstance(content, (list, bytes, bytearray, dict)):
        try:
            content = list(content)
        except TypeError:
            pass

    if isinstance(content, list):
        parts: list[ContentPart] = []
        for inner in content:
            inner_type = _get(inner, "type", "") or ""
            if inner_type == "text":
                text = _get(inner, "text", "") or ""
                if text:
                    parts.append(ContentPart(type="text", text=text))
            elif inner_type == "image":
                img = _format_image_block(inner)
                if img:
                    parts.append(img)
        if parts:
            msg["content"] = _reduce_parts(parts)
        return [msg]

    # Anything else: telemetry-log and keep the tool turn (with no content).
    _report_converter_error("AI Guard anthropic: unexpected tool_result content type %r" % (type(content).__name__,))
    return [msg]


def _format_server_tool_result_block(block: Any) -> Message:
    r"""Convert a server-managed tool result block to an AI Guard tool message.

    AI Guard sees the ``tool_use_id`` boundary plus whatever readable content
    the server tool produced:

    - ``web_search_tool_result``: ``"title: url"`` lines joined by ``"\n"``.
    - ``bash_code_execution_tool_result`` / ``code_execution_tool_result``:
      ``"STDOUT: ..."`` / ``"STDERR: ..."`` lines joined by ``"\n"``.
    - Other server tool result types: empty content (structural placeholder).
    """
    tool_use_id = _get(block, "tool_use_id", "") or ""
    block_type = _get(block, "type", "") or ""
    content = ""

    if block_type == "web_search_tool_result":
        results = _get(block, "content", []) or []
        parts = []
        for r in results:
            if _get(r, "type", "") == "web_search_result":
                title = _get(r, "title", "") or ""
                url = _get(r, "url", "") or ""
                if title:
                    parts.append(f"{title}: {url}" if url else title)
        content = "\n".join(parts)
    elif block_type in ("bash_code_execution_tool_result", "code_execution_tool_result"):
        inner = _get(block, "content")
        if inner is not None:
            stdout = _get(inner, "stdout", "") or ""
            stderr = _get(inner, "stderr", "") or ""
            parts = []
            if stdout:
                parts.append(f"STDOUT: {stdout}")
            if stderr:
                parts.append(f"STDERR: {stderr}")
            content = "\n".join(parts)

    msg = Message(role="tool", tool_call_id=tool_use_id)
    if content:
        msg["content"] = content
    return msg


# AIDEV-NOTE: Anthropic block types that are intentionally NOT mapped to AI
# Guard content -- ``redacted_thinking`` is an encrypted blob,
# ``container_upload`` / ``tool_reference`` are bare identifiers, and the
# remaining ``*_tool_result`` variants are server-managed wrappers around
# content AI Guard cannot inspect. Skipping is correct; we only log unknown
# types at debug-level so support can spot integration drift.
# NOTE: ``document`` is NOT dropped (APMSP-3286). Document blocks carry
# model-visible content (``source.type`` ``"text"`` / ``"content"`` hold
# readable text, plus ``title`` / ``context``); dropping them let a
# document-only prompt skip AI Guard evaluation entirely. See
# :func:`_format_document_block`.
_DROPPED_BLOCK_TYPES = frozenset(
    [
        "redacted_thinking",
        "container_upload",
        "tool_reference",
        "search_result",
        "web_fetch_tool_result",
        "text_editor_code_execution_tool_result",
        "tool_search_tool_result",
    ]
)

# Marker emitted for document content AI Guard cannot read as text (binary
# ``base64`` / remote ``url`` sources). Keeps a document-only message from
# producing an empty payload that would bypass evaluation (APMSP-3286).
_NON_TEXT_DOCUMENT_MARKER = "[non-text document]"


def _format_document_block(block: Any) -> list[ContentPart]:
    """Convert an Anthropic ``document`` block to scannable ContentPart(s).

    Document blocks carry model-visible content that AI Guard must scan:

    - ``source.type == "text"`` holds plain text in ``source.data``.
    - ``source.type == "content"`` nests ``text`` / ``image`` blocks.
    - ``title`` / ``context`` are model-visible strings and are included.
    - Binary (``base64``) and remote (``url``) sources cannot be read as text,
      so a placeholder marker is emitted instead.

    A document block always yields at least one part, so a message whose only
    content is a document never converts to an empty payload (which would skip
    evaluation entirely -- APMSP-3286).
    """
    parts: list[ContentPart] = []

    for field in ("title", "context"):
        value = _get(block, field, "") or ""
        if value:
            parts.append(ContentPart(type="text", text=value))

    source = _get(block, "source") or {}
    source_type = _get(source, "type", "") or ""
    if source_type == "text":
        data = _get(source, "data", "") or ""
        if data:
            parts.append(ContentPart(type="text", text=data))
    elif source_type == "content":
        inner = _get(source, "content", "")
        if isinstance(inner, str):
            if inner:
                parts.append(ContentPart(type="text", text=inner))
        else:
            if not isinstance(inner, (list, tuple)):
                try:
                    inner = list(inner)
                except TypeError:
                    inner = []
            for sub in inner or []:
                sub_type = _get(sub, "type", "") or ""
                if sub_type == "text":
                    text = _get(sub, "text", "") or ""
                    if text:
                        parts.append(ContentPart(type="text", text=text))
                elif sub_type == "image":
                    img = _format_image_block(sub)
                    if img is not None:
                        parts.append(img)
    # base64 / url / missing / unrecognised source: not text-readable.

    if not parts:
        parts.append(ContentPart(type="text", text=_NON_TEXT_DOCUMENT_MARKER))
    return parts


def _format_content_blocks(blocks: Sequence[Any]) -> _ParsedBlocks:
    """Walk a list of Anthropic content blocks once and bucket them by role.

    The split around server-tool boundaries lets the caller preserve the
    tool_call -> tool_result -> synthesis flow AI Guard expects when an
    assistant message interleaves a ``server_tool_use``, its result, and
    synthesis text.
    """
    pre_tool_parts: list[ContentPart] = []
    post_tool_parts: list[ContentPart] = []
    tool_calls: list[ToolCall] = []
    tool_results: list[Message] = []
    server_tool_results: list[Message] = []
    seen_server_tool = False

    for block in blocks:
        block_type = _get(block, "type", "")
        if block_type == "text":
            text = _get(block, "text", "")
            if text:
                part = ContentPart(type="text", text=text)
                (post_tool_parts if seen_server_tool else pre_tool_parts).append(part)
        elif block_type == "image":
            img_part = _format_image_block(block)
            if img_part is not None:
                (post_tool_parts if seen_server_tool else pre_tool_parts).append(img_part)
        elif block_type == "document":
            # Document blocks carry model-visible content that must be scanned;
            # see _format_document_block (APMSP-3286).
            target = post_tool_parts if seen_server_tool else pre_tool_parts
            target.extend(_format_document_block(block))
        elif block_type in ("tool_use", "server_tool_use"):
            tool_calls.append(_tool_call_from_block(block))
            if block_type == "server_tool_use":
                seen_server_tool = True
        elif block_type == "tool_result":
            tool_results.extend(_format_tool_result_block(block))
        elif block_type == "thinking":
            # Extended-thinking blocks carry model reasoning text that AI Guard
            # should scan; the ``signature`` field is a verification token and
            # is intentionally not included.
            text = _get(block, "thinking", "") or ""
            if text:
                part = ContentPart(type="text", text=text)
                (post_tool_parts if seen_server_tool else pre_tool_parts).append(part)
        elif block_type in (
            "web_search_tool_result",
            "bash_code_execution_tool_result",
            "code_execution_tool_result",
        ):
            server_tool_results.append(_format_server_tool_result_block(block))
        elif block_type in _DROPPED_BLOCK_TYPES:
            logger.debug("AI Guard anthropic: dropping non-scannable block type %r", block_type)
        else:
            # Unknown shape -- the SDK or Anthropic may have introduced a new
            # block type. Telemetry-log so support sees the drift instead of a
            # silent gap in the scanned payload.
            _report_converter_error("AI Guard anthropic: dropping unrecognised content block type %r" % (block_type,))

    return _ParsedBlocks(pre_tool_parts, post_tool_parts, tool_calls, tool_results, server_tool_results)


def _format_system(system: Any) -> Optional[Message]:
    r"""Convert Anthropic's top-level ``system`` kwarg to an AI Guard message.

    System can be a string or an ``Iterable[TextBlockParam]``; text blocks are
    joined by ``"\n"`` so multi-block prompts read as one coherent system
    message (matches dd-source). Returns ``None`` when nothing scannable
    survives -- AI Guard does not need an empty system turn.
    """
    if not system:
        return None
    if isinstance(system, str):
        return Message(role="system", content=system)
    if not isinstance(system, (list, bytes, bytearray)):
        try:
            system = list(system)
        except TypeError:
            return None
    if not isinstance(system, list):
        return None
    text_parts = [(_get(b, "text", "") or "") for b in system if _get(b, "type", "") == "text"]
    text = "\n".join(p for p in text_parts if p)
    if not text:
        return None
    return Message(role="system", content=text)


def _emit_assistant_with_server_tool(
    parsed: _ParsedBlocks,
) -> list[Message]:
    """Split an assistant turn that interleaves ``server_tool_use`` + results.

    AI Guard expects ``tool_call`` -> ``tool_result`` -> synthesis ordering, so
    we emit (a) one assistant message carrying the tool_calls and any pre-tool
    text, (b) the server tool result message(s), and (c) optionally a
    follow-up assistant message with the post-tool synthesis text.
    """
    pre_text = "".join((p.get("text") or "") for p in parsed.pre_tool_parts if p.get("type") == "text")
    ai_msg = Message(role="assistant", tool_calls=parsed.tool_calls)
    if pre_text:
        ai_msg["content"] = pre_text
    result: list[Message] = [ai_msg]
    result.extend(parsed.server_tool_results)
    post_text = "".join((p.get("text") or "") for p in parsed.post_tool_parts if p.get("type") == "text")
    if post_text:
        result.append(Message(role="assistant", content=post_text))
    return result


def _convert_anthropic_messages(system: Any, messages: Any) -> list[Message]:
    """Convert Anthropic ``messages`` + ``system`` to an AI Guard message list.

    Role mapping (Anthropic -> AI Guard):

    - ``system`` (top-level kwarg) -> synthetic ``role="system"`` message.
    - user message with ``content`` str/text blocks -> ``role="user"``.
    - assistant message with text -> ``role="assistant"`` (content as text).
    - assistant message with ``tool_use`` blocks -> ``role="assistant"`` with
      ``tool_calls``.
    - assistant message with ``server_tool_use`` + server result blocks ->
      assistant ``tool_calls`` message, then ``role="tool"`` result(s), then
      an optional synthesis assistant message (see
      :func:`_emit_assistant_with_server_tool`).
    - user message with ``tool_result`` blocks -> separate ``role="tool"``
      messages keyed by ``tool_use_id``; the user wrapper is suppressed when
      it would carry no text of its own.

    Returns an empty list when there is nothing to evaluate. Raises on
    converter failure (telemetry-logged); callers should fail-open by
    catching the exception and skipping evaluation rather than sending a
    half-converted payload to AI Guard.
    """
    if not messages:
        return []

    result: list[Message] = []
    try:
        sys_msg = _format_system(system)
        if sys_msg is not None:
            result.append(sys_msg)

        for msg in messages:
            if msg is None:
                continue
            role = _get(msg, "role", "")
            if not role:
                continue
            content = _get(msg, "content")

            if isinstance(content, str):
                result.append(Message(role=role, content=content))
                continue

            # In practice content is always list or tuple. Anything else is
            # malformed; emit a placeholder so AI Guard still scans something
            # rather than silently bypassing the request. NOTE: generators
            # are NOT materialised, so the SDK still iterates the original
            # blocks downstream -- AI Guard's view diverges from what
            # Anthropic actually receives in that edge case.
            if not isinstance(content, (list, tuple)):
                _report_converter_error(
                    "AI Guard anthropic: unexpected 'content' type %r on role=%r" % (type(content).__name__, role)
                )
                fallback = "[GENERATOR]" if isinstance(content, Iterator) else str(content)
                result.append(Message(role=role, content=fallback))
                continue

            parsed = _format_content_blocks(content)

            # Server tool pattern: split into tool_call -> server result(s)
            # -> optional synthesis. tool_results from user-side blocks (rare
            # in this branch but possible) are appended last to preserve
            # ordering.
            if role == "assistant" and parsed.tool_calls and parsed.server_tool_results:
                result.extend(_emit_assistant_with_server_tool(parsed))
                result.extend(parsed.tool_results)
                continue

            content_parts = parsed.pre_tool_parts + parsed.post_tool_parts

            if role == "assistant" and parsed.tool_calls:
                ai_msg = Message(role=role, tool_calls=parsed.tool_calls)
                if content_parts:
                    ai_msg["content"] = _reduce_parts(content_parts)
                result.append(ai_msg)
            elif content_parts:
                result.append(Message(role=role, content=_reduce_parts(content_parts)))
            # else: emit nothing. A user wrapper that only contained
            # tool_result blocks is suppressed -- those become standalone
            # role="tool" messages below.

            result.extend(parsed.tool_results)
    except AIGuardAbortError:
        # Block decisions surface unchanged; only converter bugs are wrapped.
        raise
    except Exception as exc:
        _report_converter_error("AI Guard anthropic: failed to convert messages", exc)
        raise

    return result


def _convert_anthropic_response(resp: Any) -> list[Message]:
    """Convert a non-streaming Anthropic ``Message`` response to AI Guard messages.

    Mirrors :func:`_convert_anthropic_messages` for the response path: a
    standard assistant turn becomes a single message; a turn that interleaves
    ``server_tool_use`` blocks gets split into tool_call + result + synthesis
    messages. Returns ``[]`` when the response is empty / malformed; raises
    only on a real converter bug (telemetry-logged).
    """
    if resp is None:
        return []
    try:
        content = _get(resp, "content")
        if not isinstance(content, list):
            return []

        parsed = _format_content_blocks(content)

        if parsed.tool_calls and parsed.server_tool_results:
            return _emit_assistant_with_server_tool(parsed)

        content_parts = parsed.pre_tool_parts + parsed.post_tool_parts

        if parsed.tool_calls:
            ai_msg = Message(role="assistant", tool_calls=parsed.tool_calls)
            if content_parts:
                ai_msg["content"] = _reduce_parts(content_parts)
            return [ai_msg]

        if content_parts:
            return [Message(role="assistant", content=_reduce_parts(content_parts))]

        return []
    except AIGuardAbortError:
        raise
    except Exception as exc:
        _report_converter_error("AI Guard anthropic: failed to convert response", exc)
        raise


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------


def _safe_convert_messages(system: Any, messages: Any) -> Optional[list[Message]]:
    """Run :func:`_convert_anthropic_messages` with a fail-open catch.

    Converter failures must remain visible (telemetry, emitted from inside
    the converter) AND must not let a half-converted payload reach AI Guard.
    Returning ``None`` signals the listener to skip evaluation entirely so
    the Anthropic SDK call proceeds normally.
    """
    try:
        return _convert_anthropic_messages(system, messages)
    except Exception:
        # _report_converter_error already emitted telemetry in the converter.
        logger.debug("AI Guard anthropic: failing open after converter error", exc_info=True)
        return None


def _safe_convert_response(resp: Any) -> Optional[list[Message]]:
    try:
        return _convert_anthropic_response(resp)
    except Exception:
        logger.debug("AI Guard anthropic: failing open after response converter error", exc_info=True)
        return None


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
        # An empty messages array is not a valid Anthropic request (the SDK
        # will reject it) and is never a valid AI Guard evaluation either.
        _report_converter_error("AI Guard anthropic: empty 'messages' kwarg; skipping evaluation")
        return None

    system = kwargs.get("system")
    # AIDEV-NOTE: Top-level ``system`` may be an Iterable[TextBlockParam].
    # Materialise generators before conversion and write them back, otherwise
    # AI Guard consumes the iterator and the SDK later serializes an exhausted
    # prompt.
    if system is not None and not isinstance(system, (str, list, bytes, bytearray, dict)):
        try:
            system = list(system)
        except TypeError:
            pass
        else:
            kwargs["system"] = system
    ai_guard_messages = _safe_convert_messages(system, messages)
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
    response_messages = _safe_convert_response(resp)
    if not response_messages:
        logger.debug("AI Guard anthropic after-hook skipped: no convertible response messages")
        return None

    request_messages = _safe_convert_messages(kwargs.get("system"), kwargs.get("messages", []))
    if request_messages is None:
        # Converter blew up on the request side; do not feed AI Guard a
        # response-only payload that misses the prompt context.
        logger.debug("AI Guard anthropic after-hook skipped: request converter failed")
        return None
    all_messages = request_messages + response_messages

    try:
        client.evaluate(all_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate Anthropic messages response", exc_info=True)
    return None
