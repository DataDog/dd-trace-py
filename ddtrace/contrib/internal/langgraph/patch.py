import os
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
    return getattr(langgraph, "__version__", "")


config._add(
    "langgraph",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_VERTEXAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_VERTEXAI_SPAN_CHAR_LIMIT", 128)),
    },
)

# @with_traced_module
# def traced_runnable_seq_invoke(langgraph, pin, func, instance, args, kwargs):
#     # might not be needed...
#     # print("---traced_runnable_seq_invoke start---")
#     steps = instance.steps
#     traced = steps[0].name not in ("_write", "_route")
#     if not traced:
#         return func(*args, **kwargs)

#     # node = steps[0]
#     # write = steps[1]
#     # setattr(node, "_dd_set_span_for_route", )
#     print("running node", steps[0].name, steps[-1].name)

#     result = func(*args, **kwargs)
#     # print("---traced_runnable_seq_invoke end---")
#     return result


@with_traced_module
def traced_runnable_callable_invoke(langgraph, pin, func, instance, args, kwargs):
    # used for tracing function executions
    node_name = instance.name
    integration: LangGraphIntegration = langgraph._datadog_integration

    # inputs = get_argument_value(args, kwargs, 0, "input")
    config = get_argument_value(args, kwargs, 1, "config")
    metadata = config.get("metadata", {}) if isinstance(config, dict) else {}
    if node_name in ("_write", "_route", "_control_branch") or metadata.get("visited", False):
        return func(*args, **kwargs)

    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, node_name),
        submit_to_llmobs=True,
        interface_type="agent",
    )

    result = None

    try:
        result = func(*args, **kwargs)
        if isinstance(
            config, dict
        ):  # this needs to be better - we need another way to see if a runnablecallable is a node vs routing function
            config["metadata"]["visited"] = True
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="node", name=node_name)
        span.finish()

    return result


@with_traced_module
def traced_pregel_invoke(langgraph, pin, func, instance, args, kwargs):
    integration: LangGraphIntegration = langgraph._datadog_integration
    # inputs = get_argument_value(args, kwargs, 0, "input")
    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, instance.name),
        submit_to_llmobs=True,
        interface_type="agent",
    )

    result = None

    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=result, operation="graph", name=instance.name
        )
        span.finish()

    return result


@with_traced_module
def patched_pregel_loop_tick(langgraph, pin, func, instance, args, kwargs):
    integration: LangGraphIntegration = langgraph._datadog_integration

    finished_tasks = getattr(instance, "tasks", {})
    result = func(*args, **kwargs)
    next_tasks = getattr(instance, "tasks", {})  # they should have been updated at this point

    integration.handle_pregel_loop_tick(finished_tasks, next_tasks, result)

    return result


def patch():
    if getattr(langgraph, "_datadog_patch", False):
        return

    langgraph._datadog_patch = True

    Pin().onto(langgraph)
    integration = LangGraphIntegration(integration_config=config.langgraph)
    langgraph._datadog_integration = integration

    # wrap("langgraph", "utils.runnable.RunnableSeq.invoke", traced_runnable_seq_invoke(langgraph))
    wrap("langgraph", "utils.runnable.RunnableCallable.invoke", traced_runnable_callable_invoke(langgraph))
    wrap("langgraph", "pregel.Pregel.invoke", traced_pregel_invoke(langgraph))
    wrap("langgraph", "pregel.loop.PregelLoop.tick", patched_pregel_loop_tick(langgraph))


def unpatch():
    if not getattr(langgraph, "_datadog_patch", False):
        return

    langgraph._datadog_patch = False

    # unwrap(langgraph.utils.runnable.RunnableSeq, "invoke")
    unwrap(langgraph.utils.runnable.RunnableCallable, "invoke")
    unwrap(langgraph.pregel.Pregel, "invoke")
    unwrap(langgraph.pregel.loop.PregelLoop, "tick")

    delattr(langgraph, "_datadog_integration")
