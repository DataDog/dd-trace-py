import sys
import os

import langgraph

from ddtrace import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.pin import Pin
from ddtrace.llmobs._integrations.langgraph import LangGraphIntegration

from typing import Dict, Any
from collections import defaultdict

def get_version():
    return getattr(langgraph, "__version__", "")

config._add(
    "langgraph",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_VERTEXAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_VERTEXAI_SPAN_CHAR_LIMIT", 128)),
    },
)

# def set_span_for_route(route)

node_invokes: Dict[str, Any] = {}

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
    integration = langgraph._datadog_integration
    
    inputs = get_argument_value(args, kwargs, 0, "input")
    config = get_argument_value(args, kwargs, 1, "config")
    metadata = config.get("metadata", {}) if isinstance(config, dict) else {}
    if node_name in ("_write", "_route", "_control_branch") or metadata.get("visited", False):
        return func(*args, **kwargs)

    node_instance_id = metadata["langgraph_checkpoint_ns"].split(":")[-1]

    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, node_name),
        submit_to_llmobs=True,
        interface_type="agent",
    )

    node_invoke = node_invokes[node_instance_id] = node_invokes.get(node_instance_id, {})
    node_invoke["span"] = {
        "trace_id": "{:x}".format(span.trace_id),
        "span_id": str(span.span_id),
    }

    result = None
    inputs = get_argument_value(args, kwargs, 0, "input")

    try:
        result = func(*args, **kwargs)
        if isinstance(config, dict): # this needs to be better - we need another way to see if a runnablecallable is a node vs routing function
            config["metadata"]["visited"] = True
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=inputs, kwargs={ **kwargs, "_dd_from": node_invoke.get("from", {}), "name": node_name }, response=result, operation="llm")
        span.finish()

    return result

@with_traced_module
def traced_pregel_invoke(langgraph, pin, func, instance, args, kwargs):
    # used for starting spans
    integration = langgraph._datadog_integration
    inputs = get_argument_value(args, kwargs, 0, "input")
    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, instance.name),
        submit_to_llmobs=True,
        interface_type="agent"
    )

    result = None

    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=inputs, kwargs=kwargs, response=result, operation="llm")
        span.finish()

    return result

@with_traced_module
def traced_pregel_loop_tick(langgraph, pin, func, instance, args, kwargs):
    finished_tasks = getattr(instance, "tasks", {})
    result = func(*args, **kwargs)
    next_tasks = getattr(instance, "tasks", {}) # they should have been updated at this point

    for task_id, task in next_tasks.items():
        task_config = getattr(task, "config", {})
        task_triggers = task_config.get("metadata", {}).get("langgraph_triggers", [])

        def extract_parent (trigger):
            split = trigger.split(":")
            if len(split) < 3:
                return split[0]
            return split[1]
        
        parent_node_names = [extract_parent(trigger) for trigger in task_triggers]
        parent_ids = [task_id for parent_node_name in parent_node_names for task_id, task in finished_tasks.items() if task.name == parent_node_name]

        for parent_id in parent_ids:
            parent_span_link = node_invokes.get(parent_id, {}).get("span", {})
            if not parent_span_link:
                continue
            node_invoke = node_invokes[task_id] = node_invokes.get(task_id, {})
            from_nodes = node_invoke["from"] = node_invoke.get("from", [])

            from_nodes.append(parent_span_link)

    return result

def patch():
    if getattr(langgraph, "_datadog_patch", False):
        return
    
    langgraph._datadog_patch = True

    from langgraph import graph

    Pin().onto(langgraph)
    integration = LangGraphIntegration(integration_config=config.langgraph) # make integration
    langgraph._datadog_integration = integration


    # wrap("langgraph", "utils.runnable.RunnableSeq.invoke", traced_runnable_seq_invoke(langgraph))
    wrap("langgraph", "utils.runnable.RunnableCallable.invoke", traced_runnable_callable_invoke(langgraph))
    wrap("langgraph", "pregel.Pregel.invoke", traced_pregel_invoke(langgraph))
    wrap("langgraph", "pregel.loop.PregelLoop.tick", traced_pregel_loop_tick(langgraph))

def unpatch():
    if not getattr(langgraph, "_datadog_patch", False):
        return
    
    langgraph._datadog_patch = False

    unwrap(langgraph.utils.runnable.RunnableSeq, "invoke")
    unwrap(langgraph.utils.runnable.RunnableCallable, "invoke")
    unwrap(langgraph.pregel.Pregel, "invoke")

    delattr(langgraph, "_datadog_integration")
