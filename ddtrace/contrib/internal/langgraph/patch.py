import sys

import langgraph

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.langgraph import LangGraphIntegration
from ddtrace.pin import Pin


def get_version():
    from langgraph import version

    return getattr(version, "__version__", "")


config._add("langgraph", {})


@with_traced_module
def traced_runnable_seq_invoke(langgraph, pin, func, instance, args, kwargs):
    """
    Traces an invocation of a RunnableSeq, which represents a node in a graph.
    Although this API is usable elsewhere, internal to LangGraph it is used to represent the
    sequence containing node invocation (function, graph, callable), the channel write, and then any routing logic.

    We utilize `instance.steps` to grab the first step as the node.

    One caveat is that if the node represents a subgraph (LangGraph), we should skip tracing at this step, as
    we will trace the graph invocation separately with `traced_pregel_invoke`. For proper span linking logic,
    we will mark the config for that graph as a subgraph.
    """
    integration: LangGraphIntegration = langgraph._datadog_integration
    node_name = instance.steps[0].name
    if node_name in ("_write", "_route"):
        return func(*args, **kwargs)
    if node_name == "LangGraph":
        config = get_argument_value(args, kwargs, 1, "config", optional=True) or {}
        config.get("metadata", {})["_dd.subgraph"] = True
        return func(*args, **kwargs)

    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, node_name),
        submit_to_llmobs=True,
    )
    result = None
    try:
        result = func(*args, **kwargs)
    except Exception:
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
    node_name = instance.steps[0].name
    if node_name in ("_write", "_route"):
        return await func(*args, **kwargs)
    if node_name == "LangGraph":
        config = get_argument_value(args, kwargs, 1, "config", optional=True) or {}
        config.get("metadata", {})["_dd.subgraph"] = True
        return await func(*args, **kwargs)

    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, node_name),
        submit_to_llmobs=True,
    )
    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="node")
        span.finish()
    return result


@with_traced_module
def traced_pregel_invoke(langgraph, pin, func, instance, args, kwargs):
    """
    Trace the invocation of a Pregel (CompiledGraph) instance.
    This operation represents the parent execution of an individual graph.
    This graph could be standalone, or embedded as a subgraph in a node of a larger graph.
    Under the hood, this graph will `tick` through until all computed tasks are completed.
    """
    integration: LangGraphIntegration = langgraph._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, instance.name),
        submit_to_llmobs=True,
    )
    result = None
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(
            span, args=args, kwargs={**kwargs, "name": instance.name}, response=result, operation="graph"
        )
        span.finish()
    return result


@with_traced_module
async def traced_pregel_ainvoke(langgraph, pin, func, instance, args, kwargs):
    """Async version of traced_pregel_invoke."""
    integration: LangGraphIntegration = langgraph._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, instance.name),
        submit_to_llmobs=True,
    )
    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(
            span, args=args, kwargs={**kwargs, "name": instance.name}, response=result, operation="graph"
        )
        span.finish()
    return result


@with_traced_module
def patched_pregel_loop_tick(langgraph, pin, func, instance, args, kwargs):
    """No tracing is done, and processing only happens if LLM Observability is enabled."""
    integration: LangGraphIntegration = langgraph._datadog_integration
    if not integration.llmobs_enabled:
        return func(*args, **kwargs)

    finished_tasks = getattr(instance, "tasks", {})
    result = func(*args, **kwargs)
    next_tasks = getattr(instance, "tasks", {})  # instance.tasks gets updated by loop.tick()
    is_subgraph_node = instance.config.get("metadata", {}).get("_dd.subgraph", False)
    integration.llmobs_handle_pregel_loop_tick(finished_tasks, next_tasks, result, is_subgraph_node)
    return result


def patch():
    if getattr(langgraph, "_datadog_patch", False):
        return

    langgraph._datadog_patch = True

    Pin().onto(langgraph)
    integration = LangGraphIntegration(integration_config=config.langgraph)
    langgraph._datadog_integration = integration

    from langgraph.pregel import Pregel
    from langgraph.pregel.loop import PregelLoop
    from langgraph.utils.runnable import RunnableSeq

    wrap(RunnableSeq, "invoke", traced_runnable_seq_invoke(langgraph))
    wrap(RunnableSeq, "ainvoke", traced_runnable_seq_ainvoke(langgraph))
    wrap(Pregel, "invoke", traced_pregel_invoke(langgraph))
    wrap(Pregel, "ainvoke", traced_pregel_ainvoke(langgraph))
    wrap(PregelLoop, "tick", patched_pregel_loop_tick(langgraph))


def unpatch():
    if not getattr(langgraph, "_datadog_patch", False):
        return

    langgraph._datadog_patch = False

    from langgraph.pregel import Pregel
    from langgraph.pregel.loop import PregelLoop
    from langgraph.utils.runnable import RunnableSeq

    unwrap(RunnableSeq, "invoke")
    unwrap(RunnableSeq, "ainvoke")
    unwrap(Pregel, "invoke")
    unwrap(Pregel, "ainvoke")
    unwrap(PregelLoop, "tick")

    delattr(langgraph, "_datadog_integration")
