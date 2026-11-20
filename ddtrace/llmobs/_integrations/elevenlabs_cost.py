"""Best-effort post-call billing using the conversation's existing synchronous SDK client."""

import time
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import start_daemon_thread
from ddtrace.llmobs._integration_api import get_llmobs_service
from ddtrace.llmobs._integration_api import is_enabled
from ddtrace.llmobs._provider_cost import build_conversation_cost_event
from ddtrace.llmobs._routing import RoutingContextType
from ddtrace.llmobs._routing import routing_targets


log = get_logger(__name__)
_active: set[tuple[int, str]] = set()
_lock = forksafe.RLock()
_MAX_ACTIVE = 8
_MAX_ATTEMPTS = 5
_BUDGET = 30.0
forksafe.register(_active.clear)


def _enabled() -> bool:
    return bool(is_enabled() and config.elevenlabs.conversation_cost_enabled)


def collect_conversation_cost(
    client: Any, conversation_id: Optional[str], routing: Optional[RoutingContextType], ml_app: str
) -> None:
    if not client or not isinstance(conversation_id, str) or not conversation_id or not _enabled():
        return
    conversations = getattr(getattr(client, "conversational_ai", None), "conversations", None)
    if conversations is None or not callable(getattr(conversations, "get", None)):
        return
    instance = getattr(get_llmobs_service(), "_instance", None)
    if instance is None:
        return
    writer = instance._llmobs_span_writer

    def enabled() -> bool:
        current = getattr(get_llmobs_service(), "_instance", None)
        return bool(_enabled() and current is instance and current._llmobs_span_writer is writer)

    key = (id(client), conversation_id)
    with _lock:
        if key in _active or len(_active) >= _MAX_ACTIVE:
            return
        _active.add(key)
    # Neither the worker nor its closures retain ConversationState or audio buffers.
    service, env, version = config.service, config.env, config.version
    targets = routing_targets(routing)
    deadline = time.monotonic() + _BUDGET

    def collect() -> None:
        try:
            for attempt in range(_MAX_ATTEMPTS):
                time.sleep(min(2**attempt, 12, max(0, deadline - time.monotonic())))
                if not enabled() or time.monotonic() >= deadline:
                    return
                try:
                    # Both SDK Conversation and AsyncConversation accept BaseElevenLabs,
                    # whose details endpoint is synchronous. Never block the asyncio loop.
                    response = conversations.get(
                        conversation_id,
                        request_options={"max_retries": 0, "timeout_in_seconds": min(3, deadline - time.monotonic())},
                    )
                    if not enabled() or time.monotonic() >= deadline:
                        return
                    event = build_conversation_cost_event(response, ml_app, service=service, env=env, version=version)
                    del response
                    if event is None:
                        continue
                    if event["session_id"] != conversation_id:
                        return
                    submitted = writer.enqueue(event, targets=targets)
                    amounts = event["meta"]["metadata"]["provider_cost"]["amounts"]
                    if submitted and all(name in amounts for name in ("total", "llm", "platform")):
                        return
                except Exception as exc:
                    status = getattr(exc, "status_code", None)
                    if isinstance(status, int) and 400 <= status < 500 and status != 429:
                        return
            log.debug("ElevenLabs automatic billing collection exhausted its retry budget")
        except Exception:
            log.debug("ElevenLabs automatic billing collection failed")
        finally:
            with _lock:
                _active.discard(key)

    try:
        # A daemon does not delay interpreter shutdown or fork waiting for HTTP.
        # The admission set is reset in forked children; no clients are replayed there.
        start_daemon_thread(collect, name="ddtrace.elevenlabs.billing")
    except Exception:
        with _lock:
            _active.discard(key)
        log.debug("Could not start ElevenLabs automatic billing collection")
