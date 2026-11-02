import inspect
import sys

import crewai
import wrapt

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration
from ddtrace.trace import tracer


def get_version() -> str:
    return getattr(crewai, "__version__", "")


logger = get_logger(__name__)


config._add("crewai", {})


def _supported_versions() -> dict[str, str]:
    return {"crewai": ">=0.102"}


def _is_streaming(instance) -> bool:
    """crewai>=1.0 streaming kickoffs return a stream at once and re-enter kickoff with stream=False to do the work.

    Only that inner call is traced, so the span covers the real execution.
    """
    return getattr(instance, "stream", False) is True


def traced_kickoff(func, instance, args, kwargs):
    if _is_streaming(instance):
        return func(*args, **kwargs)
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    instance_id = getattr(instance, "id", "")
    planning_enabled = getattr(instance, "planning", False)
    span = integration.trace(
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


async def traced_akickoff(func, instance, args, kwargs):
    if _is_streaming(instance):
        return await func(*args, **kwargs)
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    instance_id = getattr(instance, "id", "")
    planning_enabled = getattr(instance, "planning", False)
    span = integration.trace(
        "Crew Kickoff",
        span_name="CrewAI Crew",
        submit_to_llmobs=True,
        operation="crew",
        instance_id=instance_id,
        planning=planning_enabled,
    )
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="crew")
        span.finish()
    return result


def traced_task_execute(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
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


async def traced_task_aexecute(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        "CrewAI Task",
        span_name=getattr(instance, "name", ""),
        operation="task",
        instance_id=instance.id,
        submit_to_llmobs=True,
    )
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="task")
        span.finish()
    return result


def traced_task_execute_async(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    _ddtrace_ctx = integration._get_current_ctx()
    setattr(instance, "_ddtrace_ctx", _ddtrace_ctx)
    return func(*args, **kwargs)


def traced_task_get_context(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    span = tracer.current_span()
    result = func(*args, **kwargs)
    integration._llmobs_set_span_link_on_task(span, args, kwargs)
    return result


def traced_agent_execute(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        "CrewAI Agent", span_name=getattr(instance, "role", ""), operation="agent", submit_to_llmobs=True
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


async def traced_agent_aexecute(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        "CrewAI Agent", span_name=getattr(instance, "role", ""), operation="agent", submit_to_llmobs=True
    )
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="agent")
        span.finish()
    return result


def traced_tool_run(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        "CrewAI Tool", span_name=getattr(instance, "name", ""), operation="tool", submit_to_llmobs=True
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


async def traced_tool_arun(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        "CrewAI Tool", span_name=getattr(instance, "name", ""), operation="tool", submit_to_llmobs=True
    )
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="tool")
        span.finish()
    return result


def traced_base_tool_run(func, instance, args, kwargs):
    """Trace BaseTool.run, which crewai>=1.0 calls directly for native (function calling) tool calls."""
    integration: CrewAIIntegration = crewai._datadog_integration
    result = None
    span = integration.trace(
        "CrewAI Tool", span_name=getattr(instance, "name", ""), operation="tool", submit_to_llmobs=True
    )
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        tool_input = dict(kwargs) if kwargs or not args else args[0]
        tag_kwargs = {"input": tool_input, "_dd.instance": instance}
        integration.llmobs_set_tags(span, args=(), kwargs=tag_kwargs, response=result, operation="tool")
        span.finish()
    return result


def _is_internal_flow(instance) -> bool:
    """crewai>=1.0 runs its own agent executor and memory pipelines as Flow subclasses; those are not user flows."""
    flow_cls = type(instance)
    return flow_cls is not crewai.Flow and getattr(flow_cls, "__module__", "").startswith("crewai.")


async def traced_flow_kickoff(func, instance, args, kwargs):
    if _is_internal_flow(instance) or _is_streaming(instance):
        return await func(*args, **kwargs)
    integration: CrewAIIntegration = crewai._datadog_integration
    span_name = getattr(type(instance), "__name__", "CrewAI Flow")
    with integration.trace("CrewAI Flow", span_name=span_name, operation="flow", submit_to_llmobs=True) as span:
        result = await func(*args, **kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="flow")
        return result


async def traced_flow_method(func, instance, args, kwargs):
    if _is_internal_flow(instance):
        return await func(*args, **kwargs)
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
        # crewai>=1.0 returns (method_result, finished_event_id) from Flow._execute_method
        method_result = result[0] if _FLOW_EXECUTE_METHOD_RETURNS_EVENT_ID and isinstance(result, tuple) else result
        kwargs["_dd.instance"] = instance
        kwargs["_dd.initial_flow_state"] = initial_flow_state
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=method_result, operation="flow_method")
        return result


def patched_find_triggered_methods(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = func(*args, **kwargs)
    if _is_internal_flow(instance):
        return result
    current_span = tracer.current_span()
    integration.llmobs_set_span_links_on_flow(current_span, args, kwargs, instance)
    return result


def _flow_execute_method_returns_event_id() -> bool:
    try:
        return "tuple" in str(inspect.signature(crewai.Flow._execute_method).return_annotation)
    except (AttributeError, TypeError, ValueError):
        return False


_FLOW_EXECUTE_METHOD_RETURNS_EVENT_ID = False


def _defines(cls, name: str) -> bool:
    # Only wrap methods a class defines itself, so a subclass never re-wraps an inherited (already wrapped) method.
    return name in getattr(cls, "__dict__", {})


def _wrap_get_context():
    # crewai>=1.0 declares Crew._get_context as a staticmethod; wrapping the bare function would bind it
    # to the instance.
    get_context = crewai.Crew.__dict__.get("_get_context")
    if isinstance(get_context, staticmethod):
        crewai.Crew._get_context = staticmethod(wrapt.FunctionWrapper(get_context.__func__, traced_task_get_context))
    else:
        wrap(crewai, "Crew._get_context", traced_task_get_context)


def _unwrap_get_context():
    get_context = crewai.Crew.__dict__.get("_get_context")
    if isinstance(get_context, staticmethod):
        crewai.Crew._get_context = staticmethod(getattr(get_context.__func__, "__wrapped__", get_context.__func__))
    else:
        unwrap(crewai.Crew, "_get_context")


def _base_tool_classes():
    base_tool = getattr(crewai.tools, "base_tool", None)
    return [cls for cls in (getattr(base_tool, "BaseTool", None), getattr(base_tool, "Tool", None)) if cls is not None]


def patch():
    global _FLOW_EXECUTE_METHOD_RETURNS_EVENT_ID
    if getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = True

    integration: CrewAIIntegration = CrewAIIntegration(integration_config=config.crewai)
    crewai._datadog_integration = integration
    _FLOW_EXECUTE_METHOD_RETURNS_EVENT_ID = _flow_execute_method_returns_event_id()

    wrap(crewai, "Crew.kickoff", traced_kickoff)
    wrap(crewai, "Task.execute_async", traced_task_execute_async)
    wrap(crewai, "Agent.execute_task", traced_agent_execute)
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.invoke", traced_tool_run)
    wrap(crewai, "Flow.kickoff_async", traced_flow_kickoff)
    try:
        _wrap_get_context()
        wrap(crewai, "Task._execute_core", traced_task_execute)
        wrap(crewai, "Flow._execute_method", traced_flow_method)
        wrap(crewai, "Flow._find_triggered_methods", patched_find_triggered_methods)
    except AttributeError:
        logger.warning("Failed to patch internal CrewAI methods.")

    # Native async execution paths and native tool calls (crewai>=1.0)
    if _defines(crewai.Crew, "akickoff"):
        wrap(crewai, "Crew.akickoff", traced_akickoff)
    if _defines(crewai.Task, "_aexecute_core"):
        wrap(crewai, "Task._aexecute_core", traced_task_aexecute)
    if _defines(crewai.Agent, "aexecute_task"):
        wrap(crewai, "Agent.aexecute_task", traced_agent_aexecute)
    if _defines(crewai.tools.structured_tool.CrewStructuredTool, "ainvoke"):
        wrap(crewai.tools.structured_tool, "CrewStructuredTool.ainvoke", traced_tool_arun)
    for tool_cls in _base_tool_classes():
        if _defines(tool_cls, "run"):
            wrap(tool_cls, "run", traced_base_tool_run)


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
        _unwrap_get_context()
        unwrap(crewai.Task, "_execute_core")
        unwrap(crewai.Flow, "_execute_method")
        unwrap(crewai.Flow, "_find_triggered_methods")
    except AttributeError:
        pass
    if _defines(crewai.Crew, "akickoff"):
        unwrap(crewai.Crew, "akickoff")
    if _defines(crewai.Task, "_aexecute_core"):
        unwrap(crewai.Task, "_aexecute_core")
    if _defines(crewai.Agent, "aexecute_task"):
        unwrap(crewai.Agent, "aexecute_task")
    if _defines(crewai.tools.structured_tool.CrewStructuredTool, "ainvoke"):
        unwrap(crewai.tools.structured_tool.CrewStructuredTool, "ainvoke")
    for tool_cls in _base_tool_classes():
        if _defines(tool_cls, "run"):
            unwrap(tool_cls, "run")

    delattr(crewai, "_datadog_integration")
