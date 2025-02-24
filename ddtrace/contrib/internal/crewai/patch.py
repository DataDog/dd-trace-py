import os
import sys

import crewai

from ddtrace import config
from ddtrace.llmobs import LLMObs
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations import CrewAIIntegration
from ddtrace.trace import Pin


def get_version():
    # type: () -> str
    return getattr(crewai, "__version__", "")


config._add(
    "crewai",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_CREWAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_CREWAI_SPAN_CHAR_LIMIT", 128)),
    },
)


@with_traced_module
def traced_kickoff(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
    result = None
    span = integration.trace(pin, "Crew Kickoff", span_name="CrewAI Crew", submit_to_llmobs=True, operation="crew", instance_id=instance.id)
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="crew")
        span.finish()
    return result


@with_traced_module
def traced_task_execute(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
    result = None
    _ddtrace_ctx = getattr(instance, "_ddtrace_ctx", None)
    if _ddtrace_ctx:
        pin.tracer.context_provider.activate(_ddtrace_ctx[0])
        LLMObs._instance._llmobs_context_provider.activate(_ddtrace_ctx[1])
    span = integration.trace(pin, "CrewAI Task", span_name=getattr(instance, "name", ""), operation="task", instance_id=instance.id, submit_to_llmobs=True)
    if _ddtrace_ctx:
        span._set_ctx_item("_ml_obs.async_task", True)
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="task")
        span.finish()
    return result


@with_traced_module
def traced_task_execute_async(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
    result = None
    # span = integration.trace(pin, "CrewAI Task", span_name=getattr(instance, "name", ""), operation="task", instance_id=instance.id, submit_to_llmobs=True)
    curr_trace_ctx = pin.tracer.current_trace_context()
    curr_llmobs_ctx = LLMObs._instance._current_trace_context()
    setattr(instance, "_ddtrace_ctx", (curr_trace_ctx, curr_llmobs_ctx))
    result = func(*args, **kwargs)
    return result


@with_traced_module
def traced_task_get_context(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
    span = pin.tracer.current_span()
    result = func(*args, **kwargs)
    integration._llmobs_set_span_link_on_task(span, result)
    return result


@with_traced_module
def traced_agent_execute(crewai, pin, func, instance, args, kwargs):
    integration = crewai._datadog_integration
    result = None
    span = integration.trace(pin, "CrewAI Agent", span_name=getattr(instance, "role", ""), operation="agent", submit_to_llmobs=True)
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
    span = integration.trace(pin, "CrewAI Tool", span_name=getattr(instance, "name", ""), operation="tool", submit_to_llmobs=True)
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

    unwrap(crewai.Crew, "_execute_tasks")
    unwrap(crewai.Task, "_execute_core")
    unwrap(crewai.Agent, "execute_task")
    unwrap(crewai.tools.structured_tool.CrewStructuredTool, "invoke")

    delattr(crewai, "_datadog_integration")
