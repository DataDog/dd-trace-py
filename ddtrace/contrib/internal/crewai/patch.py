import sys

import crewai

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.crewai._constants import COMPONENT
from ddtrace.contrib.internal.crewai._constants import FLOW_FIND_TRIGGERED_METHODS_EVENT
from ddtrace.contrib.internal.crewai._constants import TASK_GET_CONTEXT_EVENT
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.trace import tracer


def get_version() -> str:
    return getattr(crewai, "__version__", "")


logger = get_logger(__name__)


config._add("crewai", {})


def _supported_versions() -> dict[str, str]:
    return {"crewai": ">=0.102"}


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


def _traced_call(func, instance, args, kwargs, event):
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
            _finish(ctx, error)


def traced_kickoff(func, instance, args, kwargs):
    event = _request_event("Crew Kickoff", "CrewAI Crew", "crew", args, kwargs, instance=instance)
    return _traced_call(func, instance, args, kwargs, event)


def traced_task_execute(func, instance, args, kwargs):
    _ddtrace_ctx = getattr(instance, "_ddtrace_ctx", None)
    if _ddtrace_ctx:
        # Async tasks run in a separate thread, so restore the contexts captured when they were queued.
        tracer_ctx, llmobs_ctx = _ddtrace_ctx
        tracer.context_provider.activate(tracer_ctx)
        if llmobs_ctx:
            core.dispatch("threading.execution", (llmobs_ctx,))
        delattr(instance, "_ddtrace_ctx")
    event = _request_event("CrewAI Task", getattr(instance, "name", ""), "task", args, kwargs, instance=instance)
    return _traced_call(func, instance, args, kwargs, event)


def traced_task_execute_async(func, instance, args, kwargs):
    """Capture the tracer and LLMObs contexts to propagate across threads during async task execution."""
    curr_trace_ctx = tracer.current_trace_context()
    curr_llmobs_ctx = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
        "threading.submit", ()
    ).llmobs_ctx.value
    setattr(instance, "_ddtrace_ctx", (curr_trace_ctx, curr_llmobs_ctx))
    return func(*args, **kwargs)


def traced_task_get_context(func, instance, args, kwargs):
    span = tracer.current_span()
    result = func(*args, **kwargs)
    core.dispatch(TASK_GET_CONTEXT_EVENT, (span, args, kwargs))
    return result


def traced_agent_execute(func, instance, args, kwargs):
    event = _request_event("CrewAI Agent", getattr(instance, "role", ""), "agent", args, kwargs)
    return _traced_call(func, instance, args, kwargs, event)


def traced_tool_run(func, instance, args, kwargs):
    event = _request_event("CrewAI Tool", getattr(instance, "name", ""), "tool", args, kwargs)
    return _traced_call(func, instance, args, kwargs, event)


async def traced_flow_kickoff(func, instance, args, kwargs):
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


async def traced_flow_method(func, instance, args, kwargs):
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
            event.response = await func(*args, **kwargs)
            return event.response
        # BaseException so that a cancelled flow method is recorded as an error.
        except BaseException:
            error = True
            raise
        finally:
            kwargs["_dd.instance"] = instance
            kwargs["_dd.initial_flow_state"] = initial_flow_state
            _finish(ctx, error)


def patched_find_triggered_methods(func, instance, args, kwargs):
    result = func(*args, **kwargs)
    current_span = tracer.current_span()
    core.dispatch(FLOW_FIND_TRIGGERED_METHODS_EVENT, (current_span, args, kwargs, instance))
    return result


def patch():
    if getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = True

    wrap(crewai, "Crew.kickoff", traced_kickoff)
    wrap(crewai, "Task.execute_async", traced_task_execute_async)
    wrap(crewai, "Agent.execute_task", traced_agent_execute)
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.invoke", traced_tool_run)
    wrap(crewai, "Flow.kickoff_async", traced_flow_kickoff)
    try:
        wrap(crewai, "Crew._get_context", traced_task_get_context)
        wrap(crewai, "Task._execute_core", traced_task_execute)
        wrap(crewai, "Flow._execute_method", traced_flow_method)
        wrap(crewai, "Flow._find_triggered_methods", patched_find_triggered_methods)
    except AttributeError:
        logger.warning("Failed to patch internal CrewAI methods.")

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch(COMPONENT)


def unpatch():
    if not getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = False

    integration_hooks.on_unpatch(COMPONENT)

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
