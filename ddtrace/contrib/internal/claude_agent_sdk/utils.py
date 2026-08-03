from dataclasses import replace
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr


log = get_logger(__name__)


def force_include_partial_messages(options: Any) -> tuple[Any, bool]:
    """Ensure ``options.include_partial_messages`` is True.

    Partial streaming is the only place the SDK surfaces accurate per-turn output
    tokens.

    Returns ``(options, forced)`` where ``forced`` is True only when we had to flip
    the flag ourselves. ``forced`` tells the stream handler to swallow the extra
    events (StreamEvent, SystemMessage status) so the caller's stream is unchanged.
    A defensive copy is made via ``dataclasses.replace`` so the caller's own options
    object is never mutated. When the caller already opted in, we leave the flag (and
    their stream) alone but still read the deltas.

    When ``options`` is None (the caller passed none to ``query()``), we build a default
    options object with the flag on. The SDK itself constructs a default
    ``ClaudeAgentOptions()`` when none is given, so this matches its behavior save for
    the single forced flag.
    """
    if options is None:
        try:
            from claude_agent_sdk import ClaudeAgentOptions

            return ClaudeAgentOptions(include_partial_messages=True), True
        except Exception:
            log.debug("Could not build default claude_agent_sdk options for partial messages", exc_info=True)
            return options, False
    if getattr(options, "include_partial_messages", False):
        return options, False
    try:
        return replace(options, include_partial_messages=True), True
    except Exception:
        log.debug("Could not force include_partial_messages on claude_agent_sdk options", exc_info=True)
        return options, False


def extract_partial_message_output(event: Any) -> Optional[tuple[Optional[str], Optional[int]]]:
    """Pull (message_id, output_tokens) signal out of a raw Anthropic stream event.

    ``message_start`` carries the turn's ``message.id`` (output not yet generated);
    ``message_delta`` carries the running cumulative ``usage.output_tokens`` for the
    current turn (the last one seen is the true per-turn output). Returns None for
    any other event so callers can ignore it.
    """
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    if etype == "message_start":
        message = event.get("message") or {}
        return (message.get("id"), None)
    if etype == "message_delta":
        usage = event.get("usage") or {}
        return (None, usage.get("output_tokens"))
    return None


async def _retrieve_context(instance):
    if instance is None:
        return
    try:
        # set flag to skip tracing during internal context retrieval
        instance._dd_internal_context_query = True
        await instance.query("/context")
        context_messages = []
        async for msg in instance.receive_response():
            context_messages.append(msg)
        return context_messages
    except Exception:
        log.warning("Error retrieving after context from claude_agent_sdk", exc_info=True)
    finally:
        if instance is not None:
            instance._dd_internal_context_query = False


def _extract_model_from_response(response: list[Any]) -> str:
    if not response or not isinstance(response, list):
        return ""

    for msg in response:
        msg_type = type(msg).__name__

        # check AssistantMessage.model
        if msg_type == "AssistantMessage":
            return str(_get_attr(msg, "model", None) or "")

        # check SystemMessage.data.model
        if msg_type == "SystemMessage":
            data = _get_attr(msg, "data", None)
            if data and isinstance(data, dict):
                return data.get("model") or ""

    return ""
