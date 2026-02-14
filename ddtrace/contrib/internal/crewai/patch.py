from typing import Dict

import crewai

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.events.llm import LlmRequestEvent
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration


def get_version() -> str:
    return getattr(crewai, "__version__", "")


logger = get_logger(__name__)


config._add("crewai", {})


def _supported_versions() -> Dict[str, str]:
    return {"crewai": ">=0.102"}


def _create_llm_event(pin, integration, resource, operation, kwargs, **extra_tag_kwargs):
    """Create an LlmRequestEvent for a CrewAI call."""
    base_tag_kwargs = {"operation": operation}
    base_tag_kwargs.update(extra_tag_kwargs)
    return LlmRequestEvent(
        service=int_service(pin, integration.integration_config),
        resource=resource,
        integration_name="crewai",
        provider="",
        model="",
        integration=integration,
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        base_tag_kwargs=base_tag_kwargs,
        measured=True,
    )


@with_traced_module
def traced_kickoff(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    instance_id = getattr(instance, "id", "")
    planning_enabled = getattr(instance, "planning", False)
    event = _create_llm_event(
        pin,
        integration,
        "Crew Kickoff",
        "crew",
        kwargs,
        instance_id=instance_id,
        planning=planning_enabled,
    )
    event.set_span_name("CrewAI Crew")

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            kwargs["_dd.instance"] = instance
            ctx.set_item("response", result)
            ctx.set_item("operation", "crew")
            ctx.set_item("llmobs_args", args)
    return result


@with_traced_module
def traced_task_execute(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration

    # Activate cross-thread context propagation
    ddtrace_ctx = getattr(instance, "_ddtrace_ctx", None)
    if ddtrace_ctx:
        integration.activate_distributed_context(ddtrace_ctx)

    event = _create_llm_event(
        pin,
        integration,
        "CrewAI Task",
        "task",
        kwargs,
        instance_id=instance.id,
    )
    task_name = getattr(instance, "name", "")
    if task_name:
        event.set_span_name(task_name)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            if getattr(instance, "_ddtrace_ctx", None):
                delattr(instance, "_ddtrace_ctx")
            kwargs["_dd.instance"] = instance
            ctx.set_item("response", result)
            ctx.set_item("operation", "task")
            ctx.set_item("llmobs_args", args)
    return result


@with_traced_module
def traced_task_execute_async(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    _ddtrace_ctx = integration._get_current_ctx(pin)
    setattr(instance, "_ddtrace_ctx", _ddtrace_ctx)
    return func(*args, **kwargs)


@with_traced_module
def traced_task_get_context(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = func(*args, **kwargs)
    integration.llmobs_set_span_link_on_task_from_context(args, kwargs)
    return result


@with_traced_module
def traced_agent_execute(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    event = _create_llm_event(pin, integration, "CrewAI Agent", "agent", kwargs)
    role = getattr(instance, "role", "")
    if role:
        event.set_span_name(role)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            kwargs["_dd.instance"] = instance
            ctx.set_item("response", result)
            ctx.set_item("operation", "agent")
            ctx.set_item("llmobs_args", args)
    return result


@with_traced_module
def traced_tool_run(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    event = _create_llm_event(pin, integration, "CrewAI Tool", "tool", kwargs)
    tool_name = getattr(instance, "name", "")
    if tool_name:
        event.set_span_name(tool_name)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            kwargs["_dd.instance"] = instance
            ctx.set_item("response", result)
            ctx.set_item("operation", "tool")
            ctx.set_item("llmobs_args", args)
    return result


@with_traced_module
async def traced_flow_kickoff(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    span_name = getattr(type(instance), "__name__", "CrewAI Flow")
    event = _create_llm_event(pin, integration, "CrewAI Flow", "flow", kwargs)
    event.set_span_name(span_name)

    with core.context_with_event(event) as ctx:
        result = await func(*args, **kwargs)
        ctx.set_item("response", result)
        ctx.set_item("operation", "flow")
        ctx.set_item("llmobs_args", args)
        return result


@with_traced_module
async def traced_flow_method(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    span_name = get_argument_value(args, kwargs, 0, "method_name", optional=True) or "Flow Method"
    event = _create_llm_event(
        pin,
        integration,
        "CrewAI Flow Method",
        "flow_method",
        kwargs,
        span_name=span_name,
        flow_instance=instance,
    )
    event.set_span_name(span_name)

    with core.context_with_event(event) as ctx:
        flow_state = getattr(instance, "state", {})
        initial_flow_state = {}
        if isinstance(flow_state, dict):
            initial_flow_state = {**flow_state}
        elif hasattr(flow_state, "model_dump"):
            initial_flow_state = flow_state.model_dump()
        result = await func(*args, **kwargs)
        kwargs["_dd.instance"] = instance
        kwargs["_dd.initial_flow_state"] = initial_flow_state
        ctx.set_item("response", result)
        ctx.set_item("operation", "flow_method")
        ctx.set_item("llmobs_args", args)
        return result


@with_traced_module
def patched_find_triggered_methods(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = func(*args, **kwargs)
    integration.llmobs_set_span_links_on_flow_from_context(args, kwargs, instance)
    return result


def patch():
    if getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = True

    Pin().onto(crewai)
    integration: CrewAIIntegration = CrewAIIntegration(integration_config=config.crewai)
    crewai._datadog_integration = integration

    wrap(crewai, "Crew.kickoff", traced_kickoff(crewai))
    wrap(crewai, "Task.execute_async", traced_task_execute_async(crewai))
    wrap(crewai, "Agent.execute_task", traced_agent_execute(crewai))
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.invoke", traced_tool_run(crewai))
    wrap(crewai, "Flow.kickoff_async", traced_flow_kickoff(crewai))
    try:
        wrap(crewai, "Crew._get_context", traced_task_get_context(crewai))
        wrap(crewai, "Task._execute_core", traced_task_execute(crewai))
        wrap(crewai, "Flow._execute_method", traced_flow_method(crewai))
        wrap(crewai, "Flow._find_triggered_methods", patched_find_triggered_methods(crewai))
    except AttributeError:
        logger.warning("Failed to patch internal CrewAI methods.")


def unpatch():
    if not getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = False

    unwrap(crewai.Crew, "kickoff")
    unwrap(crewai.Task, "execute_async")
    unwrap(crewai.Agent, "execute_task")
    unwrap(crewai.tools.structured_tool.CrewStructuredTool, "invoke")
    unwrap(crewai.Flow, "kickoff_async")
    try:
        unwrap(crewai.Crew, "_get_context")
        unwrap(crewai.Task, "_execute_core")
        unwrap(crewai.Flow, "_execute_method")
        unwrap(crewai.Flow, "_find_triggered_methods")
    except AttributeError:
        pass

    delattr(crewai, "_datadog_integration")
