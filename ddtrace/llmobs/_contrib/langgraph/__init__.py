from typing import Any
from typing import Optional

from ddtrace.contrib.internal.langgraph._constants import COMPONENT
from ddtrace.contrib.internal.langgraph._constants import CREATE_REACT_AGENT_EVENT
from ddtrace.contrib.internal.langgraph._constants import PREGEL_LOOP_TICK_EVENT
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import get_integration
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.langgraph import LangGraphIntegration


def _enabled_integration() -> Optional[LangGraphIntegration]:
    integration = get_integration(COMPONENT)
    if not isinstance(integration, LangGraphIntegration) or not integration.llmobs_enabled:
        return None
    return integration


def _on_create_react_agent(agent: Any, args: Any, kwargs: dict[str, Any]) -> None:
    integration = _enabled_integration()
    if integration is not None:
        integration.llmobs_handle_agent_manifest(agent, args, kwargs)


def _on_pregel_loop_tick(loop: Any, finished_tasks: Any, more_tasks: Any) -> None:
    integration = _enabled_integration()
    if integration is None:
        return
    next_tasks = getattr(loop, "tasks", {})
    is_subgraph_node = getattr(loop, "config", {}).get("metadata", {}).get("_dd.subgraph", False)
    integration.llmobs_handle_pregel_loop_tick(finished_tasks, next_tasks, more_tasks, is_subgraph_node)


def listen() -> None:
    register(COMPONENT, LangGraphIntegration)
    core.on(CREATE_REACT_AGENT_EVENT, _on_create_react_agent)
    core.on(PREGEL_LOOP_TICK_EVENT, _on_pregel_loop_tick)


def unlisten() -> None:
    unregister(COMPONENT)
    core.reset_listeners(CREATE_REACT_AGENT_EVENT, _on_create_react_agent)
    core.reset_listeners(PREGEL_LOOP_TICK_EVENT, _on_pregel_loop_tick)
