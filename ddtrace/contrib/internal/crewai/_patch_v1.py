"""Instrumentation specific to crewai>=1.0."""

from typing import Any
from typing import Callable

import crewai

from ddtrace.contrib.internal.crewai.utils import CREWAI_VERSION
from ddtrace.contrib.internal.crewai.utils import atrace_call
from ddtrace.contrib.internal.crewai.utils import trace_call
from ddtrace.contrib.internal.crewai.utils import traced_flow_kickoff
from ddtrace.contrib.internal.crewai.utils import traced_flow_method
from ddtrace.contrib.internal.crewai.utils import traced_tool_run
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value


# crewai 1.7.0 added native async execution (Crew.akickoff) and a Tool.run override that native function calling
# calls directly.
_HAS_NATIVE_ASYNC = CREWAI_VERSION >= (1, 7, 0)
# crewai 1.9.0 changed Flow._execute_method to return (method_result, finished_event_id).
_EXECUTE_METHOD_RETURNS_EVENT_ID = CREWAI_VERSION >= (1, 9, 0)


def _reenters_kickoff(instance: Any, args: Any, kwargs: Any) -> bool:
    """Streaming kickoffs (crewai>=1.6) and checkpoint restores (crewai>=1.15) return after calling kickoff again,
    with stream=False or on the restored instance. Only that inner call does the work, so the outer one is not traced.
    """
    checkpoint = get_argument_value(args, kwargs, 2, "from_checkpoint", optional=True)
    # `is True`: before crewai 1.6, Flow has no stream field, so a user flow may define its own `stream` member.
    return getattr(instance, "stream", False) is True or getattr(checkpoint, "restore_from", None) is not None


def _is_internal_flow(instance: Any) -> bool:
    """crewai 1.x runs its own agent executor and memory pipelines as Flow subclasses; those are not user flows.

    crewai.Flow itself is defined in a crewai module too, but a user can instantiate it directly.
    """
    return type(instance) is not crewai.Flow and type(instance).__module__.startswith("crewai.")


def traced_kickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    if _reenters_kickoff(instance, args, kwargs):
        return func(*args, **kwargs)
    return trace_call(func, instance, args, kwargs, "crew")


async def traced_akickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    if _reenters_kickoff(instance, args, kwargs):
        return await func(*args, **kwargs)
    return await atrace_call(func, instance, args, kwargs, "crew")


async def traced_task_aexecute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return await atrace_call(func, instance, args, kwargs, "task")


async def traced_agent_aexecute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return await atrace_call(func, instance, args, kwargs, "agent")


async def traced_user_flow_kickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    if _is_internal_flow(instance) or _reenters_kickoff(instance, args, kwargs):
        return await func(*args, **kwargs)
    return await traced_flow_kickoff(func, instance, args, kwargs)


async def traced_user_flow_method(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    if _is_internal_flow(instance):
        return await func(*args, **kwargs)
    return await traced_flow_method(func, instance, args, kwargs, returns_event_id=_EXECUTE_METHOD_RETURNS_EVENT_ID)


def patch() -> None:
    wrap(crewai, "Crew.kickoff", traced_kickoff)
    wrap(crewai, "Flow.kickoff_async", traced_user_flow_kickoff)
    wrap(crewai, "Flow._execute_method", traced_user_flow_method)
    if _HAS_NATIVE_ASYNC:
        wrap(crewai, "Crew.akickoff", traced_akickoff)
        wrap(crewai, "Task._aexecute_core", traced_task_aexecute)
        wrap(crewai, "Agent.aexecute_task", traced_agent_aexecute)
        wrap(crewai.tools.base_tool, "Tool.run", traced_tool_run)


def unpatch() -> None:
    unwrap(crewai.Crew, "kickoff")
    unwrap(crewai.Flow, "kickoff_async")
    unwrap(crewai.Flow, "_execute_method")
    if _HAS_NATIVE_ASYNC:
        unwrap(crewai.Crew, "akickoff")
        unwrap(crewai.Task, "_aexecute_core")
        unwrap(crewai.Agent, "aexecute_task")
        unwrap(crewai.tools.base_tool.Tool, "run")
