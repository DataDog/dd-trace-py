import sys
from typing import Dict

import crewai

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations import CrewAIIntegration
from ddtrace.trace import Pin


def get_version() -> str:
    return getattr(crewai, "__version__", "")


config._add("crewai", {})


def _supported_versions() -> Dict[str, str]:
    return {"crewai": ">=0.102"}


@with_traced_module
def traced_kickoff(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
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
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="crew")
        span.finish()
    return result


@with_traced_module
def traced_task_execute(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
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
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="task")
        span.finish()
    return result


@with_traced_module
def traced_task_execute_async(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
    _ddtrace_ctx = integration._get_current_ctx(pin)
    setattr(instance, "_ddtrace_ctx", _ddtrace_ctx)
    return func(*args, **kwargs)


@with_traced_module
def traced_task_get_context(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
    span = pin.tracer.current_span()
    result = func(*args, **kwargs)
    integration._llmobs_set_span_link_on_task(span, args, kwargs)
    return result


@with_traced_module
def traced_agent_execute(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
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
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="agent")
        span.finish()
    return result


@with_traced_module
def traced_tool_run(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
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
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="tool")
        span.finish()
    return result


def patch():
    if getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = True

    Pin().onto(crewai)
    integration = CrewAIIntegration(integration_config=config.crewai)
    crewai._datadog_integration = integration

    wrap(crewai, "Crew.kickoff", traced_kickoff(crewai))
    wrap(crewai, "Crew._get_context", traced_task_get_context(crewai))
    wrap(crewai, "Task._execute_core", traced_task_execute(crewai))
    wrap(crewai, "Task.execute_async", traced_task_execute_async(crewai))
    wrap(crewai, "Agent.execute_task", traced_agent_execute(crewai))
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.invoke", traced_tool_run(crewai))


def unpatch():
    if not getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = False

    unwrap(crewai.Crew, "kickoff")
    unwrap(crewai.Crew, "_get_context")
    unwrap(crewai.Task, "_execute_core")
    unwrap(crewai.Task, "execute_async")
    unwrap(crewai.Agent, "execute_task")
    unwrap(crewai.tools.structured_tool.CrewStructuredTool, "invoke")

    delattr(crewai, "_datadog_integration")
