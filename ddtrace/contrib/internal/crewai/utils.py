"""Tracing shared by every supported crewai version; see _patch_v0 and _patch_v1 for the version-specific parts."""

import sys
from typing import Any
from typing import Callable

import crewai

from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration
from ddtrace.trace import Span
from ddtrace.trace import tracer


CREWAI_VERSION = parse_version(getattr(crewai, "__version__", ""))


def _start_span(integration: CrewAIIntegration, instance: Any, operation: str) -> Span:
    if operation == "crew":
        return integration.trace(
            "Crew Kickoff",
            span_name="CrewAI Crew",
            submit_to_llmobs=True,
            operation="crew",
            instance_id=getattr(instance, "id", ""),
            planning=getattr(instance, "planning", False),
        )
    if operation == "task":
        # Set by traced_task_execute_async to parent spans run in a worker thread; only this span uses it.
        ctx = getattr(instance, "_ddtrace_ctx", None)
        if ctx:
            delattr(instance, "_ddtrace_ctx")
        return integration.trace(
            "CrewAI Task",
            span_name=getattr(instance, "name", ""),
            operation="task",
            instance_id=instance.id,
            submit_to_llmobs=True,
            _ddtrace_ctx=ctx,
        )
    if operation == "agent":
        return integration.trace(
            "CrewAI Agent", span_name=getattr(instance, "role", ""), operation="agent", submit_to_llmobs=True
        )
    # operation == "tool"
    return integration.trace(
        "CrewAI Tool", span_name=getattr(instance, "name", ""), operation="tool", submit_to_llmobs=True
    )


def _finish_span(
    integration: CrewAIIntegration,
    span: Span,
    instance: Any,
    args: Any,
    kwargs: Any,
    result: Any,
    operation: str,
) -> None:
    kwargs["_dd.instance"] = instance
    integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation=operation)
    span.finish()


def trace_call(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any, operation: str) -> Any:
    integration: CrewAIIntegration = crewai._datadog_integration
    span = _start_span(integration, instance, operation)
    result = None
    try:
        result = func(*args, **kwargs)
        return result
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        _finish_span(integration, span, instance, args, kwargs, result, operation)


async def atrace_call(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any, operation: str) -> Any:
    integration: CrewAIIntegration = crewai._datadog_integration
    span = _start_span(integration, instance, operation)
    result = None
    try:
        result = await func(*args, **kwargs)
        return result
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        _finish_span(integration, span, instance, args, kwargs, result, operation)


def traced_task_execute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return trace_call(func, instance, args, kwargs, "task")


def traced_agent_execute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return trace_call(func, instance, args, kwargs, "agent")


def traced_tool_call(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return trace_call(func, instance, args, kwargs, "tool")


async def traced_tool_acall(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return await atrace_call(func, instance, args, kwargs, "tool")


def traced_task_execute_async(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: CrewAIIntegration = crewai._datadog_integration
    _ddtrace_ctx = integration._get_current_ctx()
    setattr(instance, "_ddtrace_ctx", _ddtrace_ctx)
    return func(*args, **kwargs)


def traced_task_get_context(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: CrewAIIntegration = crewai._datadog_integration
    span = tracer.current_span()
    result = func(*args, **kwargs)
    integration._llmobs_set_span_link_on_task(span, args, kwargs)
    return result


async def trace_flow_kickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: CrewAIIntegration = crewai._datadog_integration
    span_name = getattr(type(instance), "__name__", "CrewAI Flow")
    with integration.trace("CrewAI Flow", span_name=span_name, operation="flow", submit_to_llmobs=True) as span:
        result = await func(*args, **kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="flow")
        return result


async def trace_flow_method(
    func: Callable[..., Any],
    instance: Any,
    args: Any,
    kwargs: Any,
    returns_event_id: bool = False,
) -> Any:
    """Trace Flow._execute_method. If returns_event_id, it returns (method_result, finished_event_id)."""
    integration: CrewAIIntegration = crewai._datadog_integration
    span_name = get_argument_value(args, kwargs, 0, "method_name", optional=True) or "Flow Method"
    with integration.trace(
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
        method_result = result[0] if returns_event_id else result
        kwargs["_dd.instance"] = instance
        kwargs["_dd.initial_flow_state"] = initial_flow_state
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=method_result, operation="flow_method")
        return result


def patched_find_triggered_methods(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: CrewAIIntegration = crewai._datadog_integration
    result = func(*args, **kwargs)
    current_span = tracer.current_span()
    integration.llmobs_set_span_links_on_flow(current_span, args, kwargs, instance)
    return result
