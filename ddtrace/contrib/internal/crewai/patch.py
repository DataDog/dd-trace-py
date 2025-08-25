import sys
from typing import Dict

import crewai

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration


def get_version() -> str:
    return getattr(crewai, "__version__", "")


logger = get_logger(__name__)


config._add("crewai", {})


def _supported_versions() -> Dict[str, str]:
    return {"crewai": ">=0.102"}


@with_traced_module
def traced_kickoff(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    instance_id = getattr(instance, "id", "")
    planning_enabled = getattr(instance, "planning", False)
    span = integration.trace(
        pin,
        "Crew Kickoff",
        span_name="CrewAI Crew",
        submit_to_llmobs=True,
        operation="crew",
        instance_id=instance_id,
        planning=planning_enabled,
    )
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="crew")
        span.finish()
    return result


@with_traced_module
def traced_task_execute(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        pin,
        "CrewAI Task",
        span_name=getattr(instance, "name", ""),
        operation="task",
        instance_id=instance.id,
        submit_to_llmobs=True,
        _ddtrace_ctx=getattr(instance, "_ddtrace_ctx", None),
    )
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if getattr(instance, "_ddtrace_ctx", None):
            delattr(instance, "_ddtrace_ctx")
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="task")
        span.finish()
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
    span = pin.tracer.current_span()
    result = func(*args, **kwargs)
    integration._llmobs_set_span_link_on_task(span, args, kwargs)
    return result


@with_traced_module
def traced_agent_execute(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        pin, "CrewAI Agent", span_name=getattr(instance, "role", ""), operation="agent", submit_to_llmobs=True
    )
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="agent")
        span.finish()
    return result


@with_traced_module
def traced_tool_run(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        pin, "CrewAI Tool", span_name=getattr(instance, "name", ""), operation="tool", submit_to_llmobs=True
    )
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="tool")
        span.finish()
    return result


@with_traced_module
async def traced_flow_kickoff(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    span_name = getattr(type(instance), "__name__", "CrewAI Flow")
    with integration.trace(pin, "CrewAI Flow", span_name=span_name, operation="flow", submit_to_llmobs=True) as span:
        result = await func(*args, **kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="flow")
        return result


@with_traced_module
async def traced_flow_method(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    span_name = get_argument_value(args, kwargs, 0, "method_name", optional=True) or "Flow Method"
    with integration.trace(
        pin,
        "CrewAI Flow Method",
        span_name=span_name,
        operation="flow_method",
        submit_to_llmobs=True,
        flow_instance=instance,
    ) as span:
        flow_state = getattr(instance, "state", {})
        initial_flow_state = {}
        if isinstance(flow_state, dict):
            initial_flow_state = {**flow_state}
        elif hasattr(flow_state, "model_dump"):
            initial_flow_state = flow_state.model_dump()
        result = await func(*args, **kwargs)
        kwargs["_dd.instance"] = instance
        kwargs["_dd.initial_flow_state"] = initial_flow_state
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="flow_method")
        return result


@with_traced_module
def patched_find_triggered_methods(crewai, pin, func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = func(*args, **kwargs)
    current_span = pin.tracer.current_span()
    integration.llmobs_set_span_links_on_flow(current_span, args, kwargs, instance)
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
