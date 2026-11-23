from typing import Optional

from ddtrace.contrib._events.openai_realtime import OpenAIRealtimeSessionEvent
from ddtrace.contrib.internal.openai._constants import COMPONENT
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import get_integration
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.openai import OpenAIIntegration
from ddtrace.llmobs._integrations.openai_realtime import _RealtimeState


def _enabled_integration() -> Optional[OpenAIIntegration]:
    integration = get_integration(COMPONENT)
    if not isinstance(integration, OpenAIIntegration) or not integration.llmobs_enabled:
        return None
    return integration


def _on_realtime_session(event: OpenAIRealtimeSessionEvent) -> None:
    # Realtime sessions are only observed for LLMObs, so leave the connection untouched while it is disabled.
    integration = _enabled_integration()
    if integration is not None:
        event.observer = _RealtimeState(integration, client=event.client, model=event.model)


def listen() -> None:
    register(COMPONENT, OpenAIIntegration)
    core.on(OpenAIRealtimeSessionEvent.event_name, _on_realtime_session)


def unlisten() -> None:
    unregister(COMPONENT)
    core.reset_listeners(OpenAIRealtimeSessionEvent.event_name, _on_realtime_session)
