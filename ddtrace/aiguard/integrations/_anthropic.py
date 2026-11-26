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

from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
import json
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.aiguard._api_client import _SKIPPED_REASON
from ddtrace.aiguard._api_client import AIGuardAbortError
from ddtrace.aiguard._api_client import AIGuardClient
from ddtrace.aiguard._api_client import ContentPart
from ddtrace.aiguard._api_client import Function
from ddtrace.aiguard._api_client import ImageURL
from ddtrace.aiguard._api_client import MCPServer
from ddtrace.aiguard._api_client import Message
from ddtrace.aiguard._api_client import ToolCall
from ddtrace.aiguard._common import _get
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._common import wrap_abort_error
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._context import is_aiguard_context_active
from ddtrace.aiguard.integrations._anthropic_mcp import attach_mcp
from ddtrace.aiguard.integrations._anthropic_mcp import mark_evaluated
from ddtrace.aiguard.integrations._anthropic_mcp import request_mcp_tools
from ddtrace.aiguard.integrations._anthropic_mcp import start_request
from ddtrace.aiguard.integrations._mcp import mcp_reference
from ddtrace.aiguard.integrations._mcp import mcp_server
from ddtrace.aiguard.integrations._mcp import referenced_mcp_servers
from ddtrace.internal import telemetry
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.aiguard import aiguard_config
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
        # import lazily -- ``_anthropic_errors`` pulls in the optional
        # Anthropic SDK at import time. Python's import lock guarantees all
        # concurrent cold imports observe the same class object.
        from ddtrace.aiguard.integrations._anthropic_errors import AnthropicAIGuardAbortError

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
    """Convert a single Anthropic ``tool_use`` / ``server_tool_use`` / mcp_tool_use block.

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


def _reference_connector_server(tool_call: ToolCall, block: Any, mcp_servers: dict[str, MCPServer]) -> None:
    """Reference the MCP connector server of an mcp_tool_use call by its server_name; fails open without it."""
    try:
        name = tool_call["function"]["name"]
        server_name = _get(block, "server_name")
        if not name or not isinstance(server_name, str) or not server_name:
            return
        server = mcp_servers.get(server_name)
        if server is None:
            # A server that this request does not declare is identified by its name alone.
            server = mcp_server(AI_GUARD.MCP_MANAGED_BY_PROVIDER, AI_GUARD.MCP_TRANSPORT_UNKNOWN, name=server_name)
            if server is None:
                return
            mcp_servers[server_name] = server
        # The connector exposes MCP tools to the model under their original name.
        tool_call["mcp"] = mcp_reference(server, name, name)
    except Exception:
        logger.debug("AI Guard anthropic: failed to reference the MCP connector server", exc_info=True)


def _parts_from_content_blocks(blocks: Sequence[Any]) -> list[ContentPart]:
    """Build ContentParts from nested Anthropic content blocks."""
    parts: list[ContentPart] = []
    for inner in blocks:
        inner_type = _get(inner, "type", "") or ""
        if inner_type == "text":
            text = _get(inner, "text", "") or ""
            if text:
                parts.append(ContentPart(type="text", text=text))
        elif inner_type == "image":
            img = _format_image_block(inner)
            if img:
                parts.append(img)
        elif inner_type == "document":
            parts.extend(_format_document_block(inner))
    return parts


def _format_tool_result_block(block: Any) -> list[Message]:
    """Convert a user-side ``tool_result`` block to AI Guard tool message(s)."""
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
        parts = _parts_from_content_blocks(content)
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


# Anthropic block types intentionally NOT mapped to AI Guard
# content; unknown types are logged at debug-level. ``document`` is NOT
# dropped (APMSP-3286): it carries model-visible content, see
# _format_document_block.
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

# Marker for document content that cannot be read as text (base64/url sources).
_NON_TEXT_DOCUMENT_MARKER = "[non-text document]"


def _format_document_block(block: Any) -> list[ContentPart]:
    """Convert an Anthropic ``document`` block to scannable ContentPart(s).

    Always yields at least one part (a marker for non-text sources) so a
    document-only message never converts to an empty payload (APMSP-3286).
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
            parts.extend(_parts_from_content_blocks(inner or []))
    # base64 / url / missing source: not text-readable.

    if not parts:
        parts.append(ContentPart(type="text", text=_NON_TEXT_DOCUMENT_MARKER))
    return parts


# Results of tools that Anthropic runs itself (server tools and MCP connector servers).
_SERVER_TOOL_RESULT_TYPES = frozenset(
    ("web_search_tool_result", "bash_code_execution_tool_result", "code_execution_tool_result", "mcp_tool_result")
)


def _format_content_blocks(
    blocks: Sequence[Any], role: str, mcp_servers: Optional[dict[str, MCPServer]] = None
) -> list[Message]:
    """Convert one Anthropic message's content blocks to AI Guard messages, in block order.

    A result of a tool Anthropic runs closes the assistant message before it; user turns put tool results first.
    """
    messages: list[Message] = []
    tool_results: list[Message] = []
    parts: list[ContentPart] = []
    tool_calls: list[ToolCall] = []
    # Client tool calls stay pending until the next user turn, so they end this turn, after any server result.
    pending_calls: list[ToolCall] = []

    def close_turn() -> None:
        # Only an assistant turn carries tool calls.
        calls = tool_calls if role == "assistant" else []
        if parts or calls:
            message = Message(role=role)
            if parts:
                message["content"] = _reduce_parts(list(parts))
            if calls:
                message["tool_calls"] = list(calls)
            messages.append(message)
        parts.clear()
        tool_calls.clear()

    for block in blocks:
        block_type = _get(block, "type", "")
        if block_type == "text":
            text = _get(block, "text", "")
            if text:
                parts.append(ContentPart(type="text", text=text))
        elif block_type == "image":
            img_part = _format_image_block(block)
            if img_part is not None:
                parts.append(img_part)
        elif block_type == "document":
            parts.extend(_format_document_block(block))
        elif block_type in ("tool_use", "server_tool_use", "mcp_tool_use"):
            tool_call = _tool_call_from_block(block)
            if block_type == "mcp_tool_use" and mcp_servers is not None:
                _reference_connector_server(tool_call, block, mcp_servers)
            (pending_calls if block_type == "tool_use" else tool_calls).append(tool_call)
        elif block_type == "tool_result":
            tool_results.extend(_format_tool_result_block(block))
        elif block_type in _SERVER_TOOL_RESULT_TYPES:
            if role == "assistant":
                close_turn()
                if block_type == "mcp_tool_result":
                    messages.extend(_format_tool_result_block(block))
                else:
                    messages.append(_format_server_tool_result_block(block))
        elif block_type == "thinking":
            # Extended-thinking blocks carry model reasoning text that AI Guard
            # should scan; the ``signature`` field is a verification token and
            # is intentionally not included.
            text = _get(block, "thinking", "") or ""
            if text:
                parts.append(ContentPart(type="text", text=text))
        elif block_type in _DROPPED_BLOCK_TYPES:
            logger.debug("AI Guard anthropic: dropping non-scannable block type %r", block_type)
        else:
            # Unknown shape -- the SDK or Anthropic may have introduced a new
            # block type. Telemetry-log so support sees the drift instead of a
            # silent gap in the scanned payload.
            _report_converter_error("AI Guard anthropic: dropping unrecognised content block type %r" % (block_type,))

    close_turn()
    if role != "assistant":
        # Tool results answer the previous assistant turn, so the text of this turn comes after them.
        return tool_results + messages
    if pending_calls:
        if messages and messages[-1].get("role") == "assistant":
            messages[-1]["tool_calls"] = list(messages[-1].get("tool_calls") or []) + pending_calls
        else:
            messages.append(Message(role="assistant", tool_calls=pending_calls))
    messages.extend(tool_results)
    return messages


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


def _convert_anthropic_messages(
    system: Any, messages: Any, mcp_servers: Optional[dict[str, MCPServer]] = None
) -> list[Message]:
    """Convert Anthropic ``messages`` + ``system`` to an AI Guard message list.

    Role mapping (Anthropic -> AI Guard):

    - ``system`` (top-level kwarg) -> synthetic ``role="system"`` message.
    - user message with ``content`` str/text blocks -> ``role="user"``.
    - assistant message with text -> ``role="assistant"`` (content as text).
    - assistant message with ``tool_use`` blocks -> ``role="assistant"`` with
      ``tool_calls``.
    - assistant message with ``server_tool_use`` + server result blocks ->
      assistant ``tool_calls`` message, then ``role="tool"`` result(s), then
      an optional synthesis assistant message, in block order (see
      :func:`_format_content_blocks`). MCP connector mcp_tool_use and
      mcp_tool_result blocks follow the same split.
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

            result.extend(_format_content_blocks(content, role, mcp_servers))
    except AIGuardAbortError:
        # Block decisions surface unchanged; only converter bugs are wrapped.
        raise
    except Exception as exc:
        _report_converter_error("AI Guard anthropic: failed to convert messages", exc)
        raise

    return result


def _convert_anthropic_response(resp: Any, mcp_servers: Optional[dict[str, MCPServer]] = None) -> list[Message]:
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

        # A response holds no user-side tool_result; the stream rebuild also gives that type to server tool results.
        content = [block for block in content if _get(block, "type") != "tool_result"]
        return _format_content_blocks(content, "assistant", mcp_servers)
    except AIGuardAbortError:
        raise
    except Exception as exc:
        _report_converter_error("AI Guard anthropic: failed to convert response", exc)
        raise


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------


def _safe_convert_messages(
    system: Any, messages: Any, mcp_servers: Optional[dict[str, MCPServer]] = None
) -> Optional[list[Message]]:
    """Run :func:`_convert_anthropic_messages` with a fail-open catch.

    Converter failures must remain visible (telemetry, emitted from inside
    the converter) AND must not let a half-converted payload reach AI Guard.
    Returning ``None`` signals the listener to skip evaluation entirely so
    the Anthropic SDK call proceeds normally.
    """
    try:
        return _convert_anthropic_messages(system, messages, mcp_servers)
    except Exception:
        # _report_converter_error already emitted telemetry in the converter.
        logger.debug("AI Guard anthropic: failing open after converter error", exc_info=True)
        return None


def _safe_convert_response(resp: Any, mcp_servers: Optional[dict[str, MCPServer]] = None) -> Optional[list[Message]]:
    try:
        return _convert_anthropic_response(resp, mcp_servers)
    except Exception:
        logger.debug("AI Guard anthropic: failing open after response converter error", exc_info=True)
        return None


def _is_unlisted_iterable(value: Any) -> bool:
    # SDK models iterate over their fields: they are objects, not sequences.
    return (
        isinstance(value, Iterable)
        and not isinstance(value, (list, tuple, str, bytes, Mapping))
        and not hasattr(value, "model_dump")
    )


# Blocks whose content the SDK types as an Iterable of blocks.
_ITERABLE_CONTENT_BLOCK_TYPES = frozenset(("tool_result", "mcp_tool_result"))


def materialize_request(kwargs: dict[str, Any]) -> None:
    """Read the request iterables once, so the SDK and every AI Guard hook see the same values; fails open.

    Iterable tool result content goes into a request-local block copy: the caller's objects are never changed.
    """
    try:
        _materialize_request(kwargs)
    except _CallerIterationError as exc:
        # The SDK would raise it too while reading that iterable, before sending the request.
        raise exc.__cause__ from None  # type: ignore[misc]
    except Exception:
        logger.debug("AI Guard anthropic: failed to materialize the request iterables", exc_info=True)


class _CallerIterationError(Exception):
    """Raised from an error of a caller iterable, so materialize_request lets that error through."""


def _listed(value: Iterable[Any]) -> list[Any]:
    try:
        return list(value)
    except Exception as exc:
        raise _CallerIterationError() from exc


def _materialize_request(kwargs: dict[str, Any]) -> None:
    for key in ("messages", "system", "tools", "mcp_servers"):
        if _is_unlisted_iterable(kwargs.get(key)):
            kwargs[key] = _listed(kwargs[key])
    messages = kwargs.get("messages")
    if not isinstance(messages, (list, tuple)):
        return
    copied: Optional[list[Any]] = None
    for index, message in enumerate(messages):
        content = _get(message, "content")
        if not isinstance(message, Mapping) or not isinstance(content, (list, tuple)):
            continue
        blocks: Optional[list[Any]] = None
        for block_index, block in enumerate(content):
            if (
                isinstance(block, Mapping)
                and block.get("type") in _ITERABLE_CONTENT_BLOCK_TYPES
                and _is_unlisted_iterable(block.get("content"))
            ):
                # Published at once: a later failure must not lose content that is already consumed.
                if blocks is None:
                    if copied is None:
                        copied = kwargs["messages"] = list(messages)
                    blocks = list(content)
                    copied[index] = {**message, "content": blocks}
                blocks[block_index] = {**block, "content": _listed(block["content"])}


def _connector_mcp_servers(kwargs: dict[str, Any]) -> Optional[dict[str, MCPServer]]:
    """Map each server name of the request mcp_servers (MCP connector) to its entry; None when the flag is off.

    Only name and url are read, never authorization_token. Names of one server share the entry of the first.
    """
    if not aiguard_config._ai_guard_mcp_enabled:
        return None
    try:
        declared = kwargs.get("mcp_servers")
        if declared is not None and _is_unlisted_iterable(declared):
            # Already a list when the before-hook ran; only the servers are needed here, not a new history pass.
            declared = kwargs["mcp_servers"] = list(declared)
        servers: dict[str, MCPServer] = {}
        if not isinstance(declared, (list, tuple)):
            return servers
        by_id: dict[str, MCPServer] = {}
        for entry in declared:
            name, url = _get(entry, "name"), _get(entry, "url")
            if not isinstance(name, str) or not name or name in servers:
                continue
            server = mcp_server(
                AI_GUARD.MCP_MANAGED_BY_PROVIDER,
                AI_GUARD.MCP_TRANSPORT_UNKNOWN,
                name=name,
                url=url if isinstance(url, str) else None,
            )
            if server is not None:
                servers[name] = by_id.setdefault(server["id"], server)
        return servers
    except Exception:
        logger.debug("AI Guard anthropic: failed to read the MCP connector servers", exc_info=True)
        return None


def _connector_call_ids(resp: Any) -> set[str]:
    """IDs of the mcp_tool_use calls of a response: the provider already ran them."""
    try:
        content = _get(resp, "content")
        if not isinstance(content, list):
            return set()
        ids = (_get(block, "id") for block in content if _get(block, "type") == "mcp_tool_use")
        return {call_id for call_id in ids if isinstance(call_id, str)}
    except Exception:
        logger.debug("AI Guard anthropic: failed to read the MCP connector calls of a response", exc_info=True)
        return set()


def _attach_mcp_servers(
    messages: list[Message], kwargs: dict[str, Any], connector_servers: Optional[dict[str, MCPServer]] = None
) -> tuple[Optional[list[MCPServer]], dict[str, Any]]:
    """Reference the client-managed MCP tools of this request in messages and return the mcp_servers payload.

    The payload adds the connector servers that messages reference; fails open without MCP metadata.
    """
    try:
        mcp_tools = request_mcp_tools(kwargs.get("tools"))
        servers = attach_mcp(messages, mcp_tools)
        if connector_servers:
            servers = referenced_mcp_servers(messages, (servers or []) + list(connector_servers.values()))
        return servers, mcp_tools
    except Exception:
        logger.debug("AI Guard anthropic: failed to attach MCP metadata", exc_info=True)
        return None, {}


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
    materialize_request(kwargs)
    messages = kwargs["messages"]
    if not isinstance(messages, (list, tuple)):
        logger.debug("AI Guard anthropic before-hook skipped: messages is not iterable")
        return None
    if not messages:
        # An empty messages array is not a valid Anthropic request (the SDK
        # will reject it) and is never a valid AI Guard evaluation either.
        _report_converter_error("AI Guard anthropic: empty 'messages' kwarg; skipping evaluation")
        return None

    system = kwargs.get("system")
    connector_servers = _connector_mcp_servers(kwargs)
    ai_guard_messages = _safe_convert_messages(system, messages, connector_servers)
    if not ai_guard_messages:
        logger.debug("AI Guard anthropic before-hook skipped: no convertible messages")
        return None

    # Anthropic supports final assistant turns as response prefill
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
    mcp_servers, mcp_tools = _attach_mcp_servers(ai_guard_messages, kwargs, connector_servers)
    if mcp_tools:
        # A new request with MCP tools starts a new turn: no earlier response spares its calls.
        start_request()
    try:
        evaluate_auto(client, ai_guard_messages, AI_GUARD.INTEGRATION_ANTHROPIC, mcp_servers)
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

    connector_servers = _connector_mcp_servers(kwargs)
    # Convert response first: if the model produced nothing convertible
    # (e.g. an empty content list), skip request-side conversion entirely.
    response_messages = _safe_convert_response(resp, connector_servers)
    if not response_messages:
        logger.debug("AI Guard anthropic after-hook skipped: no convertible response messages")
        return None

    request_messages = _safe_convert_messages(kwargs.get("system"), kwargs.get("messages", []), connector_servers)
    if request_messages is None:
        # Converter blew up on the request side; do not feed AI Guard a
        # response-only payload that misses the prompt context.
        logger.debug("AI Guard anthropic after-hook skipped: request converter failed")
        return None
    all_messages = request_messages + response_messages
    mcp_servers, mcp_tools = _attach_mcp_servers(all_messages, kwargs, connector_servers)

    evaluated: list[Message] = []
    try:
        evaluation = evaluate_auto(client, all_messages, AI_GUARD.INTEGRATION_ANTHROPIC, mcp_servers)
        # A middleware-skipped evaluation checked nothing, so it must not spare the tool calls their check.
        if evaluation["reason"] != _SKIPPED_REASON:
            evaluated = response_messages
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate Anthropic messages response", exc_info=True)
    finally:
        # Requests without MCP tools leave the marks alone; with them, a block or skip clears them.
        if mcp_tools:
            mark_evaluated(evaluated, mcp_tools, _connector_call_ids(resp))
    return None
