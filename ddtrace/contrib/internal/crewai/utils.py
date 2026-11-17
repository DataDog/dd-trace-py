"""Tracing shared by every supported crewai version; see _patch_v0 and _patch_v1 for the version-specific parts."""

from contextvars import ContextVar
import sys
from typing import Any
from typing import Callable
from typing import Optional

import crewai

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.crewai._constants import COMPONENT
from ddtrace.contrib.internal.crewai._constants import FLOW_FIND_TRIGGERED_METHODS_EVENT
from ddtrace.contrib.internal.crewai._constants import TASK_GET_CONTEXT_EVENT
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.trace import tracer


CREWAI_VERSION = parse_version(getattr(crewai, "__version__", ""))
# The id of the native tool call crewai is running, set by _patch_v1. The tool span hands it to LLM Observability, which
# links the span to the LLM span that requested the call.
native_tool_call_id: ContextVar[Optional[str]] = ContextVar("ddtrace_crewai_native_tool_call_id", default=None)


def _request_event(resource, span_name, operation, args, kwargs, instance=None) -> LlmRequestEvent:
    event = LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.crewai,
        service=int_service(None, config.crewai),
        resource=resource,
        provider="",
        submit_to_llmobs=True,
        request_args=args,
        # The same dict as the call's kwargs: the instance is added to it once the call returns,
        # for LLMObs to read when the request finishes.
        request_kwargs=kwargs,
        # LLMObs reads the crew, task and flow IDs from the instance when the span starts.
        instance=instance,
        operation=operation,
    )
    # Unnamed tasks, agents and tools keep the default crewai.request span name.
    if span_name:
        event.operation_name = span_name
    return event


def _finish(ctx, error):
    if error:
        # LLMObs skips output tagging on errored spans, so record the error before the request finishes.
        span_from_context(ctx).set_exc_info(*sys.exc_info())
    ctx.dispatch_ended_event()


def _traced_call(func, instance, args, kwargs, event, tool_call_id: Optional[str] = None):
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        error = False
        try:
            event.response = func(*args, **kwargs)
            return event.response
        except Exception:
            error = True
            raise
        finally:
            kwargs["_dd.instance"] = instance
            if tool_call_id is not None:
                kwargs["_dd.tool_call_id"] = tool_call_id
            _finish(ctx, error)


async def _atraced_call(func, instance, args, kwargs, event):
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        error = False
        try:
            event.response = await func(*args, **kwargs)
            return event.response
        except Exception:
            error = True
            raise
        finally:
            kwargs["_dd.instance"] = instance
            _finish(ctx, error)


def traced_kickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    event = _request_event("Crew Kickoff", "CrewAI Crew", "crew", args, kwargs, instance=instance)
    return _traced_call(func, instance, args, kwargs, event)


async def traced_akickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    event = _request_event("Crew Kickoff", "CrewAI Crew", "crew", args, kwargs, instance=instance)
    return await _atraced_call(func, instance, args, kwargs, event)


def _task_event(instance: Any, args: Any, kwargs: Any) -> LlmRequestEvent:
    return _request_event("CrewAI Task", getattr(instance, "name", ""), "task", args, kwargs, instance=instance)


def traced_task_execute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    _ddtrace_ctx = getattr(instance, "_ddtrace_ctx", None)
    if _ddtrace_ctx:
        # Async tasks run in a separate thread, so restore the contexts captured when they were queued.
        tracer_ctx, llmobs_ctx = _ddtrace_ctx
        tracer.context_provider.activate(tracer_ctx)
        if llmobs_ctx:
            core.dispatch("threading.execution", (llmobs_ctx,))
        delattr(instance, "_ddtrace_ctx")
    return _traced_call(func, instance, args, kwargs, _task_event(instance, args, kwargs))


async def traced_task_aexecute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return await _atraced_call(func, instance, args, kwargs, _task_event(instance, args, kwargs))


def traced_task_execute_async(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    """Capture the tracer and LLMObs contexts to propagate across threads during async task execution."""
    curr_trace_ctx = tracer.current_trace_context()
    curr_llmobs_ctx = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
        "threading.submit", ()
    ).llmobs_ctx.value
    setattr(instance, "_ddtrace_ctx", (curr_trace_ctx, curr_llmobs_ctx))
    return func(*args, **kwargs)


def traced_task_get_context(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    span = tracer.current_span()
    result = func(*args, **kwargs)
    core.dispatch(TASK_GET_CONTEXT_EVENT, (span, args, kwargs))
    return result


def _agent_event(instance: Any, args: Any, kwargs: Any) -> LlmRequestEvent:
    return _request_event("CrewAI Agent", getattr(instance, "role", ""), "agent", args, kwargs)


def traced_agent_execute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return _traced_call(func, instance, args, kwargs, _agent_event(instance, args, kwargs))


async def traced_agent_aexecute(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return await _atraced_call(func, instance, args, kwargs, _agent_event(instance, args, kwargs))


def _tool_event(instance: Any, args: Any, kwargs: Any, operation: str) -> LlmRequestEvent:
    return _request_event("CrewAI Tool", getattr(instance, "name", ""), operation, args, kwargs)


def traced_tool_run(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    tool_call_id = native_tool_call_id.get()
    # Cleared so that tools this tool runs, such as those of a crew it kicks off, do not take its call id.
    token = native_tool_call_id.set(None)
    try:
        return _traced_call(func, instance, args, kwargs, _tool_event(instance, args, kwargs, "tool"), tool_call_id)
    finally:
        native_tool_call_id.reset(token)


# CrewStructuredTool.invoke/ainvoke take the tool arguments as one input argument, unlike BaseTool.run and Tool.run.
def traced_tool_invoke(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return _traced_call(func, instance, args, kwargs, _tool_event(instance, args, kwargs, "tool_invoke"))


async def traced_tool_ainvoke(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return await _atraced_call(func, instance, args, kwargs, _tool_event(instance, args, kwargs, "tool_invoke"))


async def traced_flow_kickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    span_name = getattr(type(instance), "__name__", "CrewAI Flow")
    event = _request_event("CrewAI Flow", span_name, "flow", args, kwargs)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        error = False
        try:
            event.response = await func(*args, **kwargs)
            return event.response
        # BaseException so that a cancelled flow is recorded as an error.
        except BaseException:
            error = True
            raise
        finally:
            _finish(ctx, error)


async def traced_flow_method(
    func: Callable[..., Any],
    instance: Any,
    args: Any,
    kwargs: Any,
    returns_event_id: bool = False,
) -> Any:
    """Trace Flow._execute_method. If returns_event_id, it returns (method_result, finished_event_id)."""
    span_name = get_argument_value(args, kwargs, 0, "method_name", optional=True) or "Flow Method"
    event = _request_event("CrewAI Flow Method", span_name, "flow_method", args, kwargs)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        initial_flow_state = {}
        error = False
        try:
            # Inside the try so that the span is finished if reading the state raises.
            flow_state = getattr(instance, "state", {})
            if isinstance(flow_state, dict):
                initial_flow_state = {**flow_state}
            elif hasattr(flow_state, "model_dump"):
                initial_flow_state = flow_state.model_dump()
            result = await func(*args, **kwargs)
            event.response = result[0] if returns_event_id else result
            return result
        # BaseException so that a cancelled flow method is recorded as an error.
        except BaseException:
            error = True
            raise
        finally:
            kwargs["_dd.instance"] = instance
            kwargs["_dd.initial_flow_state"] = initial_flow_state
            _finish(ctx, error)


def patched_find_triggered_methods(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    current_span = tracer.current_span()
    core.dispatch(FLOW_FIND_TRIGGERED_METHODS_EVENT, (current_span, args, kwargs, instance))
    return result
