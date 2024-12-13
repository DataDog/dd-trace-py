import langgraph

from ddtrace.internal.utils import get_argument_value
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.pin import Pin

def get_version():
    return getattr(langgraph, "__version__", "")

import random

# def set_span_for_route(route)

@with_traced_module
def traced_runnable_seq_invoke(langgraph, pin, func, instance, args, kwargs):
    # might not be needed...
    # print("---traced_runnable_seq_invoke start---")
    steps = instance.steps
    traced = steps[0].name not in ("_write", "_route")
    if not traced:
        return func(*args, **kwargs)

    # node = steps[0]
    # write = steps[1]
    # setattr(node, "_dd_set_span_for_route", )

    result = func(*args, **kwargs)
    # print("---traced_runnable_seq_invoke end---")
    return result

@with_traced_module
def traced_runnable_callable_invoke(langgraph, pin, func, instance, args, kwargs):
    # used for tracing function executions
    node_name = instance.name
    # print("traced_runnable_callable_invoke", node_name, getattr(instance.func, "__name__", instance.func.__class__.__name__))
    config = args[1]
    if node_name not in ("_write", "_route", "_control_branch"):
        print("running node", node_name)
        trace_id = 'trace_id.' + str(random.random())
        span_id = 'span_id.' + str(random.random())
        link = { 'trace_id': trace_id, 'span_id': span_id }

        metadata = config["metadata"]

        old_link = metadata.get("link", None) # use this on the current span to say where we're coming from
        # breakpoint()
        metadata["link"] = link # propagate it down

    result = func(*args, **kwargs)
    # if node_name == "_control_branch":
    #     print("control_branch", result)
    return result

@with_traced_module
def traced_pregel_invoke(langgraph, pin, func, instance, args, kwargs):
    # used for starting spans
    print("\nStarting a new Pregel (Graph)")
    result = func(*args, **kwargs)
    print("Ending a Pregel (Graph)\n")
    return result

@with_traced_module
def traced_pregel_loop_tick(langgraph, pin, func, instance, args, kwargs):
    # grab instance.tasks (could not exist, grab safely)
    curr_tasks = getattr(instance, "tasks", {})
    # set on instance.config a list of where we are coming from (could be multiple tasks)
    config = getattr(instance, "config", {})
    # in prepare_single_task, use this list to set the link for the task
    links = [task.config.get("metadata", {}).get("link", {}) for task in curr_tasks.values()]
    print("my current tasks are", {task.name: task.config["metadata"]["langgraph_checkpoint_ns"] for task in curr_tasks.values()})
    result = func(*args, **kwargs)
    # how do we assign links to next tasks? we could have two links from [b, c] to [d, e], but d should have a link from b and e should have a link from c
    # for non-conditional edges, we could look at next_tasks.values()[i].config["metadata"]["langgraph_triggers"], and find the appropriate task from the curr_tasks
    next_tasks = getattr(instance, "tasks", {}) # they should have been updated at this point
    print("my next tasks are", [task.name for task in next_tasks.values()])
    print("they came from", {task.name: task.config["metadata"]["langgraph_triggers"] for task in next_tasks.values()})
    print("and their IDs are", {task.name: task.config["metadata"]["langgraph_checkpoint_ns"] for task in next_tasks.values()})
    print()
    # [task.name for task in curr_tasks.values()]
    # [task.name for task in next_tasks.values()]
    # [task.config["metadata"]["langgraph_triggers"] for task in next_tasks.values()]

    # possible triggers: "node", "prev:node", "branch:prev:router_fn:node"
    return result

@with_traced_module
def traced_pepare_single_task(langgraph, pin, func, instance, args, kwargs):
    result = func(*args, **kwargs)
    if result is not None:
        pass
    return result

def patch():
    if getattr(langgraph, "_datadog_patch", False):
        return
    
    langgraph._datadog_patch = True

    from langgraph import graph

    Pin().onto(langgraph)
    integration = None # make integration
    langgraph._datadog_integration = integration


    wrap("langgraph", "utils.runnable.RunnableSeq.invoke", traced_runnable_seq_invoke(langgraph))
    wrap("langgraph", "utils.runnable.RunnableCallable.invoke", traced_runnable_callable_invoke(langgraph))
    wrap("langgraph", "pregel.Pregel.invoke", traced_pregel_invoke(langgraph))
    wrap("langgraph", "pregel.loop.PregelLoop.tick", traced_pregel_loop_tick(langgraph))
    wrap("langgraph", "pregel.algo.prepare_single_task", traced_pepare_single_task(langgraph))

def unpatch():
    if not getattr(langgraph, "_datadog_patch", False):
        return
    
    langgraph._datadog_patch = False

    unwrap(langgraph.utils.runnable.RunnableSeq, "invoke")
    unwrap(langgraph.utils.runnable.RunnableCallable, "invoke")
    unwrap(langgraph.pregel.Pregel, "invoke")

    delattr(langgraph, "_datadog_integration")
