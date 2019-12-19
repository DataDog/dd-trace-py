from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

import multiprocessing
from multiprocessing.process import BaseProcess
from multiprocessing.pool import Pool

from ... import Pin, config
from ...context import Context
from ...ext import SpanTypes
from ...internal.logger import get_logger
from ...utils.formats import asbool, get_env
from ...utils.importlib import func_name
from ...utils.wrappers import unwrap as _u

log = get_logger(__name__)

DATADOG_PATCHED = "__datadog_patch"
DATADOG_PIN = "__dd_pin"
DATADOG_CONTEXT = "__datadog_context"

# Configure default configuration
config._add("multiprocessing", dict(
    service_name=get_env("multiprocessing", "service_name", "multiprocessing"),
    app="multiprocessing",
    distributed_tracing=asbool(get_env("multiprocessing", "distributed_tracing", True)),
))


def patch():
    if getattr(multiprocessing, DATADOG_PATCHED, False):
        return
    setattr(multiprocessing, DATADOG_PATCHED, True)

    pin = Pin(
        service=config.multiprocessing.service_name,
        app=config.multiprocessing.app
    )
    pin.onto(multiprocessing)

    _w(BaseProcess, "__init__", _patch_process_init)
    _w(BaseProcess, "run", _patch_process_run)
    _w(Pool, "__init__", _patch_pool_init)


def unpatch():
    if getattr(multiprocessing, DATADOG_PATCHED, False):
        return

    _u(BaseProcess, "__init__")
    _u(BaseProcess, "run")
    _u(Pool, "__init__")


def _patch_process_init(wrapped, instance, args, kwargs):
    pin = Pin.get_from(multiprocessing)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    ctx = pin.tracer.get_call_context()
    # save dict of context values rather than Context object to avoid pickling
    # error for Context._lock
    ctx_propagated = dict(
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        sampling_priority=ctx.sampling_priority,
        _dd_origin=ctx._dd_origin,
    )
    setattr(instance, DATADOG_CONTEXT, ctx_propagated)

    init_kwargs = kwargs.get('kwargs', {})
    init_kwargs.update({DATADOG_PIN: pin})
    kwargs['kwargs'] = init_kwargs
    wrapped(*args, **kwargs)


def _patch_process_run(wrapped, instance, args, kwargs):
    # retrieve pin from keyword arguments passed to target function
    pin = instance._kwargs.get(DATADOG_PIN)
    if pin is None:
        return wrapped(*args, **kwargs)

    # retrieve context information at time of initialization
    ctx_prop = getattr(instance, DATADOG_CONTEXT, None)
    if ctx_prop is not None:
        ctx = Context(**ctx_prop)
        pin.tracer.context_provider.activate(ctx)

    # remove pin from kwargs before calling target function
    del instance._kwargs[DATADOG_PIN]

    try:
        with pin.tracer.trace(
                "multiprocessing.run",
                service=pin.service,
                resource=func_name(wrapped),
                span_type=SpanTypes.WORKER
        ):
            return wrapped(*args, **kwargs)
    finally:
        instance._kwargs[DATADOG_PIN] = pin


def _patch_pool_init(wrapped, instance, args, kwargs):
    pin = Pin.get_from(multiprocessing)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    ctx = pin.tracer.get_call_context()
    # save dict of context values rather than Context object to avoid pickling
    # error for Context._lock
    ctx_propagated = dict(
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        sampling_priority=ctx.sampling_priority,
        _dd_origin=ctx._dd_origin,
    )
    setattr(instance, DATADOG_CONTEXT, ctx_propagated)

    setattr(instance, DATADOG_PIN, pin)

    wrapped(*args, **kwargs)
