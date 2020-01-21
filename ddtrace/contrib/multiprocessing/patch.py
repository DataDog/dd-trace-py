from ddtrace.vendor.wrapt import wrap_function_wrapper as _w, FunctionWrapper

import multiprocessing
from multiprocessing.pool import Pool

try:
    # py3
    from multiprocessing.process import BaseProcess as Process

    try:
        # works for all os but win32
        from multiprocessing.context import ForkProcess, SpawnProcess, ForkServerProcess
    except ImportError:
        ForkProcess = None
        SpawnProcess = None
        ForkServerProcess = None
except ImportError:
    from multiprocessing.process import Process

from ... import Pin, config
from ...compat import PY3
from ...context import Context
from ...ext import SpanTypes
from ...internal.logger import get_logger
from ...utils.formats import get_env
from ...utils.importlib import func_name
from ...utils.wrappers import unwrap as _u

log = get_logger(__name__)

DATADOG_PATCHED = "__datadog_patch"
DATADOG_CONTEXT = "__datadog_context"


# Configure default configuration
config._add(
    "multiprocessing",
    dict(service_name=get_env("multiprocessing", "service_name", "multiprocessing"), app="multiprocessing",),
)


def patch():
    if getattr(multiprocessing, DATADOG_PATCHED, False):
        return
    setattr(multiprocessing, DATADOG_PATCHED, True)

    pin = Pin(service=config.multiprocessing.service_name, app=config.multiprocessing.app)
    pin.onto(multiprocessing)

    _w(Process, "__init__", _patch_process_init)
    _w(Process, "run", _patch_process_run)
    _w(Pool, "_setup_queues", _patch_pool_setup_queues)

    if PY3 and (ForkProcess is not None and SpawnProcess is not None and ForkServerProcess is not None):
        _w(ForkProcess, "__init__", _patch_process_init)
        _w(SpawnProcess, "__init__", _patch_process_init)
        _w(ForkServerProcess, "__init__", _patch_process_init)


def unpatch():
    if getattr(multiprocessing, DATADOG_PATCHED, False):
        return

    _u(Process, "__init__")
    _u(Process, "run")


def _wrap_task_function(wrapped):
    def wrapper(*args, **kwargs):
        pin = Pin.get_from(multiprocessing)
        process = multiprocessing.current_process()
        process_ctx = getattr(process, DATADOG_CONTEXT)

        if not pin or not pin.enabled() or process_ctx is None:
            return wrapped(*args, **kwargs)

        # unpack ctx private attribute
        trace_id, span_id, sampling_priority = process_ctx

        # activate cloned context
        ctx = Context(trace_id=trace_id, span_id=span_id, sampling_priority=sampling_priority)
        pin.tracer.context_provider.activate(ctx.clone())

        with pin.tracer.trace(
            "multiprocessing", service=pin.service, resource=func_name(wrapped), span_type=SpanTypes.WORKER
        ):
            return wrapped(*args, **kwargs)

    return wrapper


def _wrapper_task_get(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    # wrap each task in iterable
    task_iterable = result[3]
    task_iterable = tuple([(_wrap_task_function(t[0]),) + t[1:] for t in task_iterable])
    result = result[:3] + (task_iterable,) + result[4:]
    return result


def _patch_pool_setup_queues(wrapped, instance, args, kwargs):
    pin = Pin.get_from(multiprocessing)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    wrapped(*args, **kwargs)

    # patch get method on task queue in order to wrap task before it is called
    # by pool workers
    instance._inqueue.get = FunctionWrapper(instance._inqueue.get, _wrapper_task_get)


def _patch_process_init(wrapped, instance, args, kwargs):
    pin = Pin.get_from(multiprocessing)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    ctx = pin.tracer.get_call_context()
    wrapped(*args, **kwargs)

    process_ctx = None
    if ctx.trace_id is not None:
        process_ctx = (ctx.trace_id, ctx.span_id, ctx.sampling_priority)
    setattr(instance, DATADOG_CONTEXT, process_ctx)


def _patch_process_run(wrapped, instance, args, kwargs):
    pin = Pin.get_from(multiprocessing)
    process_ctx = getattr(instance, DATADOG_CONTEXT)

    if not pin or not pin.enabled() or process_ctx is None:
        return wrapped(*args, **kwargs)

    # unpack ctx private attribute
    trace_id, span_id, sampling_priority = process_ctx

    # activate cloned context
    ctx = Context(trace_id=trace_id, span_id=span_id, sampling_priority=sampling_priority)
    pin.tracer.context_provider.activate(ctx.clone())

    with pin.tracer.trace(
        "multiprocessing.run", service=pin.service, resource=func_name(wrapped), span_type=SpanTypes.WORKER
    ):
        return wrapped(*args, **kwargs)
