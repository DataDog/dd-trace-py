import sys

import langgraph

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.langgraph._constants import COMPONENT
from ddtrace.contrib.internal.langgraph._constants import CONSUME_AITER_EVENT
from ddtrace.contrib.internal.langgraph._constants import CREATE_REACT_AGENT_EVENT
from ddtrace.contrib.internal.langgraph._constants import PREGEL_LOOP_TICK_EVENT
from ddtrace.contrib.internal.langgraph._constants import SPAN_TRACES_ASTREAM
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal._exceptions import DDBlockException
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.trace import tracer


def get_version():
    from langgraph import version

    return getattr(version, "__version__", "")


try:
    from langgraph.pregel import Pregel as LangGraphPregel
except ImportError:
    LangGraphPregel = None

# Control flow exceptions used by LangGraph for workflow management
# These should not be treated as errors in APM traces:
# - ParentCommand: Used for subgraph navigation and control flow
# - GraphInterrupt: Used for human-in-the-loop workflows (interrupt())
try:
    from langgraph.errors import ParentCommand as LangGraphParentCommandError
except ImportError:
    LangGraphParentCommandError = None

try:
    from langgraph.errors import GraphInterrupt as LangGraphGraphInterruptError
except ImportError:
    LangGraphGraphInterruptError = None


def _is_langgraph_control_flow(exc):
    """Return True if ``exc`` is a LangGraph control-flow signal (``GraphInterrupt`` or
    ``ParentCommand``) rather than a real error. LangGraph uses these for human-in-the-loop
    pauses and subgraph routing, so the span must not be marked as an error.
    """
    return (LangGraphParentCommandError is not None and isinstance(exc, LangGraphParentCommandError)) or (
        LangGraphGraphInterruptError is not None and isinstance(exc, LangGraphGraphInterruptError)
    )


LANGGRAPH_VERSION = parse_version(get_version())

LANGGRAPH_MODULE_MAP = {
    "langgraph._internal._runnable": "langgraph.utils.runnable",
}


def _get_module_name(module_name: str) -> str:
    """Normalize the module name to the original module name used for langgraph<0.6.0 to avoid breaking changes"""
    return LANGGRAPH_MODULE_MAP.get(module_name, module_name)


def _supported_versions() -> dict[str, str]:
    return {"langgraph": "*"}


config._add("langgraph", {})


def _get_node_name(instance):
    """Gets the name of the first step in a RunnableSeq instance as the node name."""
    steps = getattr(instance, "steps", [])
    first_step = steps[0] if steps else None
    return getattr(first_step, "name", None), first_step


def _should_trace_node(instance, args: tuple, kwargs: dict) -> tuple[bool, str]:
    """
    Determines if a node should be traced. If the first step is a writing or routing step, or
    the node represents a subgraph, we should not trace it. If the node is a subgraph, mark it
    as such in the config metadata for use in `traced_pregel_loop_tick`.

    Returns a tuple of (should_trace, node_name)
    """
    node_name, first_step = _get_node_name(instance)
    if node_name in ("_write", "_route"):
        return False, node_name
    if (LangGraphPregel and isinstance(first_step, LangGraphPregel)) or node_name == "LangGraph":
        config = get_argument_value(args, kwargs, 1, "config", optional=True) or {}
        config.get("metadata", {})["_dd.subgraph"] = True
        return False, node_name
    return True, node_name


def _resource(instance, name) -> str:
    return "%s.%s.%s" % (_get_module_name(instance.__module__), instance.__class__.__name__, name)


def _request_event(resource, operation, args, kwargs, instance=None) -> LlmRequestEvent:
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.langgraph,
        service=int_service(None, config.langgraph),
        resource=resource,
        provider=None,
        submit_to_llmobs=True,
        request_args=args,
        request_kwargs=kwargs,
        # LLMObs reads the graph from the instance when the span starts.
        instance=instance,
        operation=operation,
    )


def _record_error(ctx, exc):
    """Mark the request's span as errored, unless exc is LangGraph control flow. Call from an except block."""
    if not _is_langgraph_control_flow(exc):
        # LLMObs reads the error when the request finishes, so record it before then.
        span_from_context(ctx).set_exc_info(*sys.exc_info())


def traced_runnable_seq_invoke(func, instance, args, kwargs):
    """
    Traces an invocation of a RunnableSeq, which represents a node in a graph.
    It represents the sequence containing node invocation (function, graph, callable), the channel write,
    and then any routing logic.

    We utilize `instance.steps` to grab the first step as the node.

    One caveat is that if the node represents a subgraph (LangGraph), we should skip tracing at this step, as
    we will trace the graph invocation separately with `traced_pregel_stream`.
    """
    should_trace, node_name = _should_trace_node(instance, args, kwargs)
    if not should_trace:
        return func(*args, **kwargs)

    event = _request_event(_resource(instance, node_name), "node", args, kwargs)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            event.response = func(*args, **kwargs)
            return event.response
        except (DDBlockException, Exception) as e:
            _record_error(ctx, e)
            raise
        finally:
            ctx.dispatch_ended_event()


async def traced_runnable_seq_ainvoke(func, instance, args, kwargs):
    """Async version of traced_runnable_seq_invoke."""
    should_trace, node_name = _should_trace_node(instance, args, kwargs)
    if not should_trace:
        return await func(*args, **kwargs)

    event = _request_event(_resource(instance, node_name), "node", args, kwargs)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            event.response = await func(*args, **kwargs)
            return event.response
        except (DDBlockException, Exception) as e:
            _record_error(ctx, e)
            raise
        finally:
            ctx.dispatch_ended_event()


def traced_runnable_seq_astream(func, instance, args, kwargs):
    """
    This function returns a generator wrapper that yields the results of RunnableSeq.astream(),
    ending the span after the stream is consumed, otherwise following the logic of traced_runnable_seq_ainvoke().
    """
    should_trace, node_name = _should_trace_node(instance, args, kwargs)
    if not should_trace:
        return func(*args, **kwargs)

    event = _request_event(_resource(instance, node_name), "node", args, kwargs)
    # The span stays active after the context exits and is finished once the stream is consumed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        span_from_context(ctx)._set_ctx_item(SPAN_TRACES_ASTREAM, True)
        try:
            result = func(*args, **kwargs)
        except (DDBlockException, Exception) as e:
            _record_error(ctx, e)
            ctx.dispatch_ended_event()
            raise

    async def _astream():
        item = None
        response = None
        stream_exc = None
        add_supported = True
        try:
            while True:
                try:
                    item = await result.__anext__()
                    if add_supported:
                        try:
                            response = item if response is None else response + item
                        except TypeError:
                            response = item
                            add_supported = False
                    else:
                        # default to the last item if addition between items is not supported
                        response = item
                    yield item
                except StopAsyncIteration:
                    break
                # do not widen to bare ``BaseException`` here - this wraps a ``yield``, so
                # ``GeneratorExit`` / ``CancelledError`` from normal stream teardown must not be caught
                # (they'd be mis-reported as span errors). The ``finally`` below still closes the span.
                except (DDBlockException, Exception) as e:
                    _record_error(ctx, e)
                    stream_exc = e
                    raise
        finally:
            # Report the partial output on any non-error exit (including ``GeneratorExit`` abandonment);
            # a real propagated exception drops it.
            event.response = None if stream_exc is not None else response
            ctx.dispatch_ended_event()

    return _astream()


async def traced_runnable_seq_consume_aiter(func, instance, args, kwargs):
    """
    Modifies the span tracing RunnableSeq.astream() to internally include its final output, as that iterator
    does not yield the final output in versions >=0.3.29. Instead, the final output is aggregated
    and returned as a single value by _consume_aiter().
    """
    output = await func(*args, **kwargs)

    span = tracer.current_span()
    if span is not None and span._get_ctx_item(SPAN_TRACES_ASTREAM):
        core.dispatch(CONSUME_AITER_EVENT, (span, output))

    return output


def traced_pregel_stream(func, instance, args, kwargs):
    """
    Trace the streaming of a Pregel (CompiledGraph) instance.
    This operation represents the parent execution of an individual graph.
    This graph could be standalone, or embedded as a subgraph in a node of a larger graph.
    Under the hood, this graph will `tick` through until all computed tasks are completed.

    Calling `invoke` on a graph calls `stream` under the hood.
    """
    name = getattr(instance, "name", "LangGraph")
    # A copy, so that the graph name LLMObs reads is not passed to the wrapped call.
    event = _request_event(_resource(instance, name), "graph", args, {**kwargs, "name": name}, instance=instance)
    # The span stays active after the context exits and is finished once the stream is consumed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = func(*args, **kwargs)
        except (DDBlockException, Exception) as e:
            _record_error(ctx, e)
            ctx.dispatch_ended_event()
            raise

    def _stream():
        item = None
        response = None
        try:
            while True:
                try:
                    item = next(result)
                    yield item
                except StopIteration:
                    response = item[-1] if isinstance(item, tuple) else item
                    break
                except (DDBlockException, Exception) as e:
                    _record_error(ctx, e)
                    raise
        finally:
            # finish in ``finally`` so the span closes on every exit, including early
            # abandonment - a consumer ``break`` tears the generator down via ``GeneratorExit``, which is
            # intentionally not caught above, so teardown is not flagged as an error. ``response`` stays
            # ``None`` unless the stream ran to completion, so abandonment reports no misleading output.
            event.response = response
            ctx.dispatch_ended_event()

    return _stream()


def traced_pregel_astream(func, instance, args, kwargs):
    """Async version of traced_pregel_stream."""
    name = getattr(instance, "name", "LangGraph")
    # A copy, so that the graph name LLMObs reads is not passed to the wrapped call.
    event = _request_event(_resource(instance, name), "graph", args, {**kwargs, "name": name}, instance=instance)
    # The span stays active after the context exits and is finished once the stream is consumed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = func(*args, **kwargs)
        except (DDBlockException, Exception) as e:
            _record_error(ctx, e)
            ctx.dispatch_ended_event()
            raise

    async def _astream():
        item = None
        response = None
        try:
            while True:
                try:
                    item = await result.__anext__()
                    yield item
                except StopAsyncIteration:
                    response = item[-1] if isinstance(item, tuple) else item
                    break
                except (DDBlockException, Exception) as e:
                    _record_error(ctx, e)
                    raise
        finally:
            # finish in ``finally`` so the span closes on every exit, including early
            # abandonment - a consumer ``break`` on ``__interrupt__`` (human-in-the-loop) tears the generator
            # down via ``GeneratorExit``, which is intentionally not caught above, so teardown is not
            # flagged as an error. ``response`` stays ``None`` unless the stream ran to completion.
            event.response = response
            ctx.dispatch_ended_event()

    return _astream()


def patched_create_react_agent(func, instance, args, kwargs):
    agent = func(*args, **kwargs)

    core.dispatch(CREATE_REACT_AGENT_EVENT, (agent, args, kwargs))

    return agent


def patched_pregel_loop_tick(func, instance, args, kwargs):
    """No tracing is done; LLMObs links the graph's tasks from the dispatched event."""
    finished_tasks = getattr(instance, "tasks", {})
    result = func(*args, **kwargs)  # replaces instance.tasks with the newly queued tasks
    core.dispatch(PREGEL_LOOP_TICK_EVENT, (instance, finished_tasks, result))
    return result


def patch():
    graph_patched = getattr(langgraph, "_datadog_patch", False)

    if not graph_patched:
        _patch_graph_modules(langgraph)

    try:
        # langgraph.prebuilt imports langgraph.graph, causing circular import errors
        # catch this error and patch it on the *second* attempt, since we run import
        # hooks for both langgraph.graph and langgraph.prebuilt.
        from langgraph import prebuilt

        prebuilt_patched = getattr(prebuilt, "_datadog_patch", False)
        if not prebuilt_patched:
            wrap(prebuilt, "create_react_agent", patched_create_react_agent)
            setattr(prebuilt, "_datadog_patch", True)
    except (ImportError, AttributeError):
        # this is possible when the module is not fully loaded yet,
        # as prebuilt imports langgraph.graph under the hood
        pass


def _patch_graph_modules(langgraph):
    langgraph._datadog_patch = True

    from langgraph.pregel import Pregel

    if LANGGRAPH_VERSION < (0, 6, 0):
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq
    else:
        from langgraph._internal._runnable import RunnableSeq
        from langgraph.pregel._loop import PregelLoop

    wrap(RunnableSeq, "invoke", traced_runnable_seq_invoke)
    wrap(RunnableSeq, "ainvoke", traced_runnable_seq_ainvoke)
    # trace `astream` and `consume_aiter` since they are triggered by `astream_events` ->`Pregel.astream`
    # The sync counter-parts are not used anywhere as of langgraph 0.4.7, so we don't trace them for now.
    wrap(RunnableSeq, "astream", traced_runnable_seq_astream)
    wrap(Pregel, "stream", traced_pregel_stream)
    wrap(Pregel, "astream", traced_pregel_astream)
    wrap(PregelLoop, "tick", patched_pregel_loop_tick)

    if LANGGRAPH_VERSION >= (0, 3, 29):
        if LANGGRAPH_VERSION < (0, 6, 0):
            wrap(langgraph.utils.runnable, "_consume_aiter", traced_runnable_seq_consume_aiter)
        else:
            wrap(langgraph._internal._runnable, "_consume_aiter", traced_runnable_seq_consume_aiter)

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch(COMPONENT)


def unpatch():
    if getattr(langgraph, "_datadog_patch", False):
        langgraph._datadog_patch = False

        integration_hooks.on_unpatch(COMPONENT)

        from langgraph import prebuilt
        from langgraph.pregel import Pregel

        if LANGGRAPH_VERSION < (0, 6, 0):
            from langgraph.pregel.loop import PregelLoop
            from langgraph.utils.runnable import RunnableSeq
        else:
            from langgraph._internal._runnable import RunnableSeq
            from langgraph.pregel._loop import PregelLoop

        unwrap(RunnableSeq, "invoke")
        unwrap(RunnableSeq, "ainvoke")
        unwrap(RunnableSeq, "astream")
        unwrap(Pregel, "stream")
        unwrap(Pregel, "astream")
        unwrap(PregelLoop, "tick")

        if LANGGRAPH_VERSION >= (0, 3, 29):
            if LANGGRAPH_VERSION < (0, 6, 0):
                unwrap(langgraph.utils.runnable, "_consume_aiter")
            else:
                unwrap(langgraph._internal._runnable, "_consume_aiter")

    if hasattr(langgraph, "prebuilt") and getattr(langgraph.prebuilt, "_datadog_patch", False):
        langgraph.prebuilt._datadog_patch = False
        unwrap(prebuilt, "create_react_agent")
