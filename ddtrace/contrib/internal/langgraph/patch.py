import langgraph

from ddtrace.internal.utils import get_argument_value
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.pin import Pin

def get_version():
    return getattr(langgraph, "__version__", "")

# def set_span_for_route(route)

@with_traced_module
def traced_runnable_seq_invoke(langgraph, pin, func, instance, args, kwargs):
    print("---traced_runnable_seq_invoke start---")
    steps = instance.steps
    traced = steps[0].name not in ("_write", "_route")
    if not traced:
        return func(*args, **kwargs)

    # node = steps[0]
    # write = steps[1]
    # setattr(node, "_dd_set_span_for_route", )

    result = func(*args, **kwargs)
    print("---traced_runnable_seq_invoke end---")
    return result

@with_traced_module
def traced_runnable_callable_invoke(langgraph, pin, func, instance, args, kwargs):
    print("traced_runnable_callable_invoke", instance.name, getattr(instance.func, "__name__", instance.func.__class__.__name__))
    return func(*args, **kwargs)

@with_traced_module
def traced_pregel_invoke(langgraph, pin, func, instance, args, kwargs):
    print("\n----traced_pregel_invoke start----")
    result = func(*args, **kwargs)
    print("----traced_pregel_invoke end----\n")
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

def unpatch():
    if not getattr(langgraph, "_datadog_patch", False):
        return
    
    langgraph._datadog_patch = False

    unwrap(langgraph.utils.runnable.RunnableSeq, "invoke")
    unwrap(langgraph.utils.runnable.RunnableCallable, "invoke")

    delattr(langgraph, "_datadog_integration")
