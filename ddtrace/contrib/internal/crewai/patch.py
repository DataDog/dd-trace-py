import sys

import crewai

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration
from ddtrace.trace import tracer


def get_version() -> str:
    return getattr(crewai, "__version__", "")


CREWAI_VERSION = parse_version(get_version())

logger = get_logger(__name__)


config._add("crewai", {})


def _supported_versions() -> dict[str, str]:
    return {"crewai": ">=0.102"}


def _reenters_kickoff(instance, args, kwargs) -> bool:
    """Streaming kickoffs (crewai>=1.6) and checkpoint restores (crewai>=1.15) return after calling kickoff again,
    with stream=False or on the restored instance. Only that inner call does the work, so the outer one is not traced.
    """
    checkpoint = get_argument_value(args, kwargs, 2, "from_checkpoint", optional=True)
    # `is True`: before crewai 1.6, Flow has no stream field, so a user flow may define its own `stream` member.
    return getattr(instance, "stream", False) is True or getattr(checkpoint, "restore_from", None) is not None


def _start_span(integration, instance, operation):
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


def _finish_span(integration, span, instance, args, kwargs, result, operation):
    kwargs["_dd.instance"] = instance
    integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation=operation)
    span.finish()


def _trace(func, instance, args, kwargs, operation):
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


async def _atrace(func, instance, args, kwargs, operation):
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


def traced_kickoff(func, instance, args, kwargs):
    if _reenters_kickoff(instance, args, kwargs):
        return func(*args, **kwargs)
    return _trace(func, instance, args, kwargs, "crew")


async def traced_akickoff(func, instance, args, kwargs):
    if _reenters_kickoff(instance, args, kwargs):
        return await func(*args, **kwargs)
    return await _atrace(func, instance, args, kwargs, "crew")


def traced_task_execute(func, instance, args, kwargs):
    return _trace(func, instance, args, kwargs, "task")


async def traced_task_aexecute(func, instance, args, kwargs):
    return await _atrace(func, instance, args, kwargs, "task")


def traced_agent_execute(func, instance, args, kwargs):
    return _trace(func, instance, args, kwargs, "agent")


async def traced_agent_aexecute(func, instance, args, kwargs):
    return await _atrace(func, instance, args, kwargs, "agent")


def traced_tool_call(func, instance, args, kwargs):
    return _trace(func, instance, args, kwargs, "tool")


async def traced_tool_acall(func, instance, args, kwargs):
    return await _atrace(func, instance, args, kwargs, "tool")


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


def _is_internal_flow(instance) -> bool:
    """crewai 1.x runs its own agent executor and memory pipelines as Flow subclasses; those are not user flows.

    crewai.Flow itself is defined in a crewai module too, but a user can instantiate it directly.
    """
    return type(instance) is not crewai.Flow and type(instance).__module__.startswith("crewai.")


async def traced_flow_kickoff(func, instance, args, kwargs):
    if _is_internal_flow(instance) or _reenters_kickoff(instance, args, kwargs):
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
        # crewai 1.9.0 changed Flow._execute_method to return (method_result, finished_event_id).
        method_result = result[0] if CREWAI_VERSION >= (1, 9, 0) else result
        kwargs["_dd.instance"] = instance
        kwargs["_dd.initial_flow_state"] = initial_flow_state
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=method_result, operation="flow_method")
        return result


def patched_find_triggered_methods(func, instance, args, kwargs):
    integration: CrewAIIntegration = crewai._datadog_integration
    result = func(*args, **kwargs)
    current_span = tracer.current_span()
    integration.llmobs_set_span_links_on_flow(current_span, args, kwargs, instance)
    return result


def patch():
    if getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = True

    integration: CrewAIIntegration = CrewAIIntegration(integration_config=config.crewai)
    crewai._datadog_integration = integration

    wrap(crewai, "Crew.kickoff", traced_kickoff)
    wrap(crewai, "Task.execute_async", traced_task_execute_async)
    wrap(crewai, "Agent.execute_task", traced_agent_execute)
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.invoke", traced_tool_call)
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.ainvoke", traced_tool_acall)
    wrap(crewai.tools.base_tool, "BaseTool.run", traced_tool_call)
    wrap(crewai, "Flow.kickoff_async", traced_flow_kickoff)
    try:
        wrap(crewai, "Crew._get_context", traced_task_get_context)
        wrap(crewai, "Task._execute_core", traced_task_execute)
        wrap(crewai, "Flow._execute_method", traced_flow_method)
        wrap(crewai, "Flow._find_triggered_methods", patched_find_triggered_methods)
    except AttributeError:
        logger.warning("Failed to patch internal CrewAI methods.")

    # crewai 1.7.0 added native async execution (Crew.akickoff) and a Tool.run override that native function
    # calling calls directly.
    if CREWAI_VERSION >= (1, 7, 0):
        wrap(crewai, "Crew.akickoff", traced_akickoff)
        wrap(crewai, "Task._aexecute_core", traced_task_aexecute)
        wrap(crewai, "Agent.aexecute_task", traced_agent_aexecute)
        wrap(crewai.tools.base_tool, "Tool.run", traced_tool_call)


def unpatch():
    if not getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = False

    unwrap(crewai.Crew, "kickoff")
    unwrap(crewai.Task, "execute_async")
    unwrap(crewai.Agent, "execute_task")
    unwrap(crewai.tools.structured_tool.CrewStructuredTool, "invoke")
    unwrap(crewai.tools.structured_tool.CrewStructuredTool, "ainvoke")
    unwrap(crewai.tools.base_tool.BaseTool, "run")
    unwrap(crewai.Flow, "kickoff_async")
    try:
        # unwrap() reads the attribute through the class, which would drop the staticmethod of crewai>=1.7.
        crewai.Crew._get_context = crewai.Crew.__dict__["_get_context"].__wrapped__
        unwrap(crewai.Task, "_execute_core")
        unwrap(crewai.Flow, "_execute_method")
        unwrap(crewai.Flow, "_find_triggered_methods")
    except (AttributeError, KeyError):
        pass
    if CREWAI_VERSION >= (1, 7, 0):
        unwrap(crewai.Crew, "akickoff")
        unwrap(crewai.Task, "_aexecute_core")
        unwrap(crewai.Agent, "aexecute_task")
        unwrap(crewai.tools.base_tool.Tool, "run")

    delattr(crewai, "_datadog_integration")
