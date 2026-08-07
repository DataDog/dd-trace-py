from dataclasses import replace
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr


log = get_logger(__name__)


def _caller_opted_into_partial_messages(options: Any) -> bool:
    """Whether the caller already asked for partial streaming.

    A caller can opt in two ways: the typed ``include_partial_messages`` field, or the
    ``extra_args`` escape hatch (``extra_args={"include-partial-messages": None}``), which
    the SDK renders as the same ``--include-partial-messages`` CLI flag. Either counts as
    an opt-in, so we neither force (avoiding a duplicate CLI flag) nor filter their stream.
    """
    return getattr(options, "include_partial_messages", False) or "include-partial-messages" in (
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

            return ClaudeAgentOptions(include_partial_messages=True), True
        except Exception:
            log.debug("Could not build default claude_agent_sdk options for partial messages", exc_info=True)
            return options, False
    if _caller_opted_into_partial_messages(options):
        return options, False
    try:
        if in_place:
            options.include_partial_messages = True
            return options, True
        return replace(options, include_partial_messages=True), True
    except Exception:
        log.debug("Could not force include_partial_messages on claude_agent_sdk options", exc_info=True)
        return options, False


_PARTIAL_INPUT_USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def extract_partial_message_usage(event: Any) -> Optional[tuple[Optional[str], dict]]:
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
