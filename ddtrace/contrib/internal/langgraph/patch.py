import sys
from typing import Dict

import langgraph

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.constants import LANGGRAPH_ASTREAM_OUTPUT
from ddtrace.llmobs._integrations.constants import LANGGRAPH_SPAN_TRACES_ASTREAM
from ddtrace.llmobs._integrations.langgraph import LangGraphIntegration
from ddtrace.trace import Pin


def get_version():
    from langgraph import version

    return getattr(version, "__version__", "")


try:
    from langgraph.pregel import Pregel as LangGraphPregel
except ImportError:
    LangGraphPregel = None

try:
    from langgraph.errors import ParentCommand as LangGraphParentCommandError
except ImportError:
    LangGraphParentCommandError = None

LANGGRAPH_VERSION = parse_version(get_version())

LANGGRAPH_MODULE_MAP = {
    "langgraph._internal._runnable": "langgraph.utils.runnable",
}


def _get_module_name(module_name: str) -> str:
    """Normalize the module name to the original module name used for langgraph<0.6.0 to avoid breaking changes"""
    return LANGGRAPH_MODULE_MAP.get(module_name, module_name)


def _supported_versions() -> Dict[str, str]:
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


@with_traced_module
def traced_runnable_seq_invoke(langgraph, pin, func, instance, args, kwargs):
    """
    Traces an invocation of a RunnableSeq, which represents a node in a graph.
    It represents the sequence containing node invocation (function, graph, callable), the channel write,
    and then any routing logic.

    We utilize `instance.steps` to grab the first step as the node.

    One caveat is that if the node represents a subgraph (LangGraph), we should skip tracing at this step, as
    we will trace the graph invocation separately with `traced_pregel_stream`.
    """
    integration: LangGraphIntegration = langgraph._datadog_integration

    should_trace, node_name = _should_trace_node(instance, args, kwargs)
    if not should_trace:
        return func(*args, **kwargs)

    span = integration.trace(
        pin,
        "%s.%s.%s" % (_get_module_name(instance.__module__), instance.__class__.__name__, node_name),
        submit_to_llmobs=True,
    )
    result = None
    try:
        result = func(*args, **kwargs)
    except Exception as e:
        if LangGraphParentCommandError is None or not isinstance(e, LangGraphParentCommandError):
            span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="node")
        span.finish()
    return result


@with_traced_module
async def traced_runnable_seq_ainvoke(langgraph, pin, func, instance, args, kwargs):
    """Async version of traced_runnable_seq_invoke."""
    integration: LangGraphIntegration = langgraph._datadog_integration

    should_trace, node_name = _should_trace_node(instance, args, kwargs)
    if not should_trace:
        return await func(*args, **kwargs)

    span = integration.trace(
        pin,
        "%s.%s.%s" % (_get_module_name(instance.__module__), instance.__class__.__name__, node_name),
        submit_to_llmobs=True,
    )
    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception as e:
        if LangGraphParentCommandError is None or not isinstance(e, LangGraphParentCommandError):
            span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="node")
        span.finish()
    return result


@with_traced_module
def traced_runnable_seq_astream(langgraph, pin, func, instance, args, kwargs):
    """
    This function returns a generator wrapper that yields the results of RunnableSeq.astream(),
    ending the span after the stream is consumed, otherwise following the logic of traced_runnable_seq_ainvoke().
    """
    integration: LangGraphIntegration = langgraph._datadog_integration

    should_trace, node_name = _should_trace_node(instance, args, kwargs)
    if not should_trace:
        return func(*args, **kwargs)

    span = integration.trace(
        pin,
        "%s.%s.%s" % (_get_module_name(instance.__module__), instance.__class__.__name__, node_name),
        submit_to_llmobs=True,
    )

    span._set_ctx_item(LANGGRAPH_SPAN_TRACES_ASTREAM, True)

    result = None

    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="node")
        span.finish()
        raise

    async def _astream():
        item = None
        response = None
        add_supported = True
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
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response, operation="node")
                span.finish()
                break
            except Exception as e:
                if LangGraphParentCommandError is None or not isinstance(e, LangGraphParentCommandError):
                    # This error is caught in the LangGraph framework, we shouldn't mark it as a runtime error.
                    span.set_exc_info(*sys.exc_info())
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="node")
                span.finish()
                raise

    return _astream()


@with_traced_module
async def traced_runnable_seq_consume_aiter(langgraph, pin: Pin, func, instance, args, kwargs):
    """
    Modifies the span tracing RunnableSeq.astream() to internally include its final output, as that iterator
    does not yield the final output in versions >=0.3.29. Instead, the final output is aggregated
    and returned as a single value by _consume_aiter().
    """
    integration: LangGraphIntegration = langgraph._datadog_integration
    output = await func(*args, **kwargs)

    if integration.llmobs_enabled:
        span = pin.tracer.current_span()
        if not span:
            return output

        from_astream = span._get_ctx_item(LANGGRAPH_SPAN_TRACES_ASTREAM) or False
        if from_astream:
            span._set_ctx_item(LANGGRAPH_ASTREAM_OUTPUT, output)

    return output


@with_traced_module
def traced_pregel_stream(langgraph, pin, func, instance, args, kwargs):
    """
    Trace the streaming of a Pregel (CompiledGraph) instance.
    This operation represents the parent execution of an individual graph.
    This graph could be standalone, or embedded as a subgraph in a node of a larger graph.
    Under the hood, this graph will `tick` through until all computed tasks are completed.

    Calling `invoke` on a graph calls `stream` under the hood.
    """
    integration: LangGraphIntegration = langgraph._datadog_integration
    name = getattr(instance, "name", "LangGraph")
    span = integration.trace(
        pin,
        "%s.%s.%s" % (_get_module_name(instance.__module__), instance.__class__.__name__, name),
        submit_to_llmobs=True,
        instance=instance,
    )

    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs={**kwargs, "name": name}, response=None, operation="graph")
        span.finish()
        raise

    def _stream():
        item = None
        while True:
            try:
                item = next(result)
                yield item
            except StopIteration:
                response = item[-1] if isinstance(item, tuple) else item
                integration.llmobs_set_tags(
                    span,
                    args=args,
                    kwargs={**kwargs, "name": name},
                    response=response,
                    operation="graph",
                )
                span.finish()
                break
            except Exception as e:
                if LangGraphParentCommandError is None or not isinstance(e, LangGraphParentCommandError):
                    span.set_exc_info(*sys.exc_info())
                integration.llmobs_set_tags(
                    span,
                    args=args,
                    kwargs={**kwargs, "name": name},
                    response=None,
                    operation="graph",
                )
                span.finish()
                raise

    return _stream()


@with_traced_module
def traced_pregel_astream(langgraph, pin, func, instance, args, kwargs):
    """Async version of traced_pregel_stream."""
    integration: LangGraphIntegration = langgraph._datadog_integration
    name = getattr(instance, "name", "LangGraph")
    span = integration.trace(
        pin,
        "%s.%s.%s" % (_get_module_name(instance.__module__), instance.__class__.__name__, name),
        submit_to_llmobs=True,
        instance=instance,
    )

    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs={**kwargs, "name": name}, response=None, operation="graph")
        span.finish()
        raise

    async def _astream():
        item = None
        while True:
            try:
                item = await result.__anext__()
                yield item
            except StopAsyncIteration:
                response = item[-1] if isinstance(item, tuple) else item
                integration.llmobs_set_tags(
                    span,
                    args=args,
                    kwargs={**kwargs, "name": name},
                    response=response,
                    operation="graph",
                )
                span.finish()
                break
            except Exception as e:
                if LangGraphParentCommandError is None or not isinstance(e, LangGraphParentCommandError):
                    span.set_exc_info(*sys.exc_info())
                integration.llmobs_set_tags(
                    span,
                    args=args,
                    kwargs={**kwargs, "name": name},
                    response=None,
                    operation="graph",
                )
                span.finish()
                raise

    return _astream()


@with_traced_module
def patched_create_react_agent(langgraph, pin, func, instance, args, kwargs):
    integration: LangGraphIntegration = langgraph._datadog_integration
    agent = func(*args, **kwargs)

    integration.llmobs_handle_agent_manifest(agent, args, kwargs)

    return agent


@with_traced_module
def patched_pregel_loop_tick(langgraph, pin, func, instance, args, kwargs):
    """No tracing is done, and processing only happens if LLM Observability is enabled."""
    integration: LangGraphIntegration = langgraph._datadog_integration
    if not integration.llmobs_enabled:
        return func(*args, **kwargs)

    finished_tasks = getattr(instance, "tasks", {})
    result = func(*args, **kwargs)
    next_tasks = getattr(instance, "tasks", {})  # instance.tasks gets updated by loop.tick()
    is_subgraph_node = getattr(instance, "config", {}).get("metadata", {}).get("_dd.subgraph", False)
    integration.llmobs_handle_pregel_loop_tick(finished_tasks, next_tasks, result, is_subgraph_node)
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
            wrap(prebuilt, "create_react_agent", patched_create_react_agent(langgraph))
            setattr(prebuilt, "_datadog_patch", True)
    except (ImportError, AttributeError):
        # this is possible when the module is not fully loaded yet,
        # as prebuilt imports langgraph.graph under the hood
        pass


def _patch_graph_modules(langgraph):
    langgraph._datadog_patch = True

    Pin().onto(langgraph)
    integration = LangGraphIntegration(integration_config=config.langgraph)
    langgraph._datadog_integration = integration

    from langgraph.pregel import Pregel

    if LANGGRAPH_VERSION < (0, 6, 0):
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq
    else:
        from langgraph._internal._runnable import RunnableSeq
        from langgraph.pregel._loop import PregelLoop

    wrap(RunnableSeq, "invoke", traced_runnable_seq_invoke(langgraph))
    wrap(RunnableSeq, "ainvoke", traced_runnable_seq_ainvoke(langgraph))
    # trace `astream` and `consume_aiter` since they are triggered by `astream_events` ->`Pregel.astream`
    # The sync counter-parts are not used anywhere as of langgraph 0.4.7, so we don't trace them for now.
    wrap(RunnableSeq, "astream", traced_runnable_seq_astream(langgraph))
    wrap(Pregel, "stream", traced_pregel_stream(langgraph))
    wrap(Pregel, "astream", traced_pregel_astream(langgraph))
    wrap(PregelLoop, "tick", patched_pregel_loop_tick(langgraph))

    if LANGGRAPH_VERSION >= (0, 3, 29):
        if LANGGRAPH_VERSION < (0, 6, 0):
            wrap(langgraph.utils.runnable, "_consume_aiter", traced_runnable_seq_consume_aiter(langgraph))
        else:
            wrap(langgraph._internal._runnable, "_consume_aiter", traced_runnable_seq_consume_aiter(langgraph))


def unpatch():
    if getattr(langgraph, "_datadog_patch", False):
        langgraph._datadog_patch = False

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

        delattr(langgraph, "_datadog_integration")

    if hasattr(langgraph, "prebuilt") and getattr(langgraph.prebuilt, "_datadog_patch", False):
        langgraph.prebuilt._datadog_patch = False
        unwrap(prebuilt, "create_react_agent")
