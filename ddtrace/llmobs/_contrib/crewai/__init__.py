from typing import Any
from typing import Optional

from ddtrace.contrib.internal.crewai._constants import COMPONENT
from ddtrace.contrib.internal.crewai._constants import FLOW_FIND_TRIGGERED_METHODS_EVENT
from ddtrace.contrib.internal.crewai._constants import TASK_GET_CONTEXT_EVENT
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import get_integration
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration


def _integration() -> Optional[CrewAIIntegration]:
    integration = get_integration(COMPONENT)
    return integration if isinstance(integration, CrewAIIntegration) else None


def _on_task_get_context(span: Any, args: Any, kwargs: Any) -> None:
    integration = _integration()
    if integration is not None:
        integration._llmobs_set_span_link_on_task(span, args, kwargs)


def _on_flow_find_triggered_methods(flow_span: Any, args: Any, kwargs: Any, flow_instance: Any) -> None:
    integration = _integration()
    if integration is not None:
        integration.llmobs_set_span_links_on_flow(flow_span, args, kwargs, flow_instance)


def listen() -> None:
    register(COMPONENT, CrewAIIntegration)
    core.on(TASK_GET_CONTEXT_EVENT, _on_task_get_context)
    core.on(FLOW_FIND_TRIGGERED_METHODS_EVENT, _on_flow_find_triggered_methods)


def unlisten() -> None:
    unregister(COMPONENT)
    core.reset_listeners(TASK_GET_CONTEXT_EVENT, _on_task_get_context)
    core.reset_listeners(FLOW_FIND_TRIGGERED_METHODS_EVENT, _on_flow_find_triggered_methods)
