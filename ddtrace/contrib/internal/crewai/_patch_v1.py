"""Instrumentation specific to crewai>=1.0."""

import importlib
from typing import Any
from typing import Callable
from typing import Iterator

import crewai

from ddtrace.contrib.internal.crewai.utils import CREWAI_VERSION
from ddtrace.contrib.internal.crewai.utils import atrace_call
from ddtrace.contrib.internal.crewai.utils import native_tool_call_id
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
_HAS_STREAMING = CREWAI_VERSION >= (1, 6, 0)
_HAS_CHECKPOINTS = CREWAI_VERSION >= (1, 15, 0)
# Where each crewai version runs a native tool call, knowing the id the LLM gave it: (module, owner class, function).
_NATIVE_TOOL_CALL_FUNCTIONS = (
    ("crewai.agents.crew_agent_executor", "CrewAgentExecutor", "_execute_single_native_tool_call"),
    ("crewai.agents.agent_executor", "AgentExecutor", "_execute_single_native_tool_call"),
    ("crewai.agents.step_executor", None, "execute_single_native_tool_call"),
)


def _reenters_kickoff(instance: Any, args: Any, kwargs: Any) -> bool:
    """Streaming kickoffs and checkpoint restores return after calling kickoff again, with stream=False or on the
    restored instance. Only that inner call does the work, so the outer one is not traced.
    """
    if _HAS_STREAMING and getattr(instance, "stream", False):
        return True
    if _HAS_CHECKPOINTS:
        checkpoint = get_argument_value(args, kwargs, 2, "from_checkpoint", optional=True)
        return getattr(checkpoint, "restore_from", None) is not None
    return False


def _native_tool_call_functions() -> Iterator[tuple[Any, str]]:
    for module_name, owner_name, function_name in _NATIVE_TOOL_CALL_FUNCTIONS:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        owner = getattr(module, owner_name) if owner_name else module
        if hasattr(owner, function_name):
            yield owner, function_name


def traced_native_tool_call(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    """Make the call id visible to the tool span. Tools run concurrently, so it is kept in a context variable."""
    call_id = kwargs.get("call_id")
    if call_id is None:
        # crewai>=1.12 passes the raw tool call instead, in whichever format the LLM provider returned it.
        from crewai.utilities.agent_utils import extract_tool_call_info

        info = extract_tool_call_info(get_argument_value(args, kwargs, 0, "tool_call"))
        call_id = info[0] if info else None
    token = native_tool_call_id.set(call_id)
    try:
        return func(*args, **kwargs)
    finally:
        native_tool_call_id.reset(token)


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
    for owner, function_name in _native_tool_call_functions():
        wrap(owner, function_name, traced_native_tool_call)


def unpatch() -> None:
    unwrap(crewai.Crew, "kickoff")
    unwrap(crewai.Flow, "kickoff_async")
    unwrap(crewai.Flow, "_execute_method")
    if _HAS_NATIVE_ASYNC:
        unwrap(crewai.Crew, "akickoff")
        unwrap(crewai.Task, "_aexecute_core")
        unwrap(crewai.Agent, "aexecute_task")
        unwrap(crewai.tools.base_tool.Tool, "run")
    for owner, function_name in _native_tool_call_functions():
        unwrap(owner, function_name)
