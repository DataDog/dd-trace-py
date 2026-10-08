from dataclasses import replace
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr


log = get_logger(__name__)


# Records whether the caller explicitly passed include_partial_messages at construction. The
# field defaults to False, so afterward an explicit False is indistinguishable from the default
# and an explicit True from a value we forced; this marker preserves the caller's real intent.
DD_PARTIAL_EXPLICIT_ATTR = "_dd_include_partial_messages_explicit"


def _caller_set_partial_messages(options: Any) -> bool:
    """Whether the caller made any explicit choice about partial streaming: setting the typed
    include_partial_messages field (True or False), or using the extra_args escape hatch
    ({"include-partial-messages": None}) that renders the same CLI flag. In every such case we
    leave the flag and their stream alone — honoring an opt-out and not double-setting an opt-in.

    Keys off the marker, not the live value, so a flag we forced on a reused object is not mistaken
    for a caller choice (which would stop us filtering the events we injected).
    """
    return bool(getattr(options, DD_PARTIAL_EXPLICIT_ATTR, False)) or "include-partial-messages" in (
        getattr(options, "extra_args", None) or {}
    )


def force_include_partial_messages(options: Any, in_place: bool = False) -> tuple[Any, bool]:
    """Ensure ``options.include_partial_messages`` is True.

    Partial streaming is the only place the SDK surfaces accurate per-turn output
    tokens.

    Returns ``(options, forced)`` where ``forced`` is True only when we had to flip
    the flag ourselves. ``forced`` tells the stream handler to swallow the extra
    events (StreamEvent, SystemMessage status) so the caller's stream is unchanged.
    When the caller already opted in, we leave the flag (and their stream) alone but
    still read the deltas.

    ``in_place`` selects how we flip the flag:

    - ``False`` (the ``query()`` path): return a defensive copy via ``dataclasses.replace``
      so the caller's throwaway per-call kwarg — which they may reuse across calls — is
      never mutated. When ``options`` is None, build a default ``ClaudeAgentOptions`` with
      the flag on (matching the SDK, which constructs a default when none is given).
    - ``True`` (the ``ClaudeSDKClient`` path): mutate the given object in place and return
      it. The client stores the caller's options (``self.options = options``) and reads its
      fields lazily at ``connect()`` time, so swapping in a copy would silently drop any field the caller mutates after
      construction. The SDK has already ensured ``instance.options`` is a real
      ``ClaudeAgentOptions`` by the time we run, so ``options`` is never None here.
    """
    if options is None:
        try:
            from claude_agent_sdk import ClaudeAgentOptions

            # Build via a plain constructor + assignment so the marker stays False (this default is
            # one we forced, not a caller choice); passing the kwarg would mark it explicit.
            forced = ClaudeAgentOptions()
            forced.include_partial_messages = True
            return forced, True
        except Exception:
            log.debug("Could not build default claude_agent_sdk options for partial messages", exc_info=True)
            return options, False
    if _caller_set_partial_messages(options):
        return options, False
    try:
        if in_place:
            options.include_partial_messages = True
            return options, True
        # replace() re-runs __init__ with every field as a kwarg, so it would mark the copy as
        # explicit; reset the marker since this copy is one we forced, not a caller choice.
        copy = replace(options, include_partial_messages=True)
        setattr(copy, DD_PARTIAL_EXPLICIT_ATTR, False)
        return copy, True
    except Exception:
        log.debug("Could not force include_partial_messages on claude_agent_sdk options", exc_info=True)
        return options, False


_PARTIAL_INPUT_USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def extract_partial_message_usage(event: Any) -> Optional[tuple[Optional[str], dict[str, int]]]:
    """Pull a (message_id, usage) signal out of a raw Anthropic stream event.

    ``message_start`` carries the turn's ``message.id`` and the input-side usage
    (``input_tokens`` plus the cache counts); its ``output_tokens`` is only a
    pre-generation snapshot, so we drop it. ``message_delta`` carries the running
    cumulative ``output_tokens`` for the current turn (the last one seen is the true
    per-turn output). Returns None for any other event so callers can ignore it.

    Mining both sides lets the handler report token counts even on SDK versions predating
    ``AssistantMessage.usage`` (< 0.1.49), where these stream events are the only source.
    """
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    if etype == "message_start":
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        input_usage = {k: usage[k] for k in _PARTIAL_INPUT_USAGE_KEYS if usage.get(k) is not None}
        return (message.get("id"), input_usage)
    if etype == "message_delta":
        usage = event.get("usage") or {}
        output_tokens = usage.get("output_tokens")
        return (None, {} if output_tokens is None else {"output_tokens": output_tokens})
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
