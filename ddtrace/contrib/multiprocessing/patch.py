from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

import multiprocessing

try:
    # py3
    from multiprocessing.process import BaseProcess as Process
except ImportError:
    from multiprocessing.process import Process

from ... import Pin, config
from ...ext import SpanTypes
from ...internal.logger import get_logger
from ...utils.formats import get_env
from ...utils.importlib import func_name
from ...utils.wrappers import unwrap as _u

log = get_logger(__name__)

DATADOG_PATCHED = "__datadog_patch"
DATADOG_PIN = "__dd_pin"
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


def unpatch():
    if getattr(multiprocessing, DATADOG_PATCHED, False):
        return

    _u(Process, "__init__")
    _u(Process, "run")


def _patch_process_init(wrapped, instance, args, kwargs):
    pin = Pin.get_from(multiprocessing)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    ctx = pin.tracer.get_call_context().clone()
    datadog_kwargs = {DATADOG_PIN: pin, DATADOG_CONTEXT: ctx}

    # ``kwargs`` argument to Process can either be the 5th and final positional
    # argument or a named arguments
    kwargs_in_args = len(args) == 5

    if kwargs_in_args:
        init_kwargs = args[-1]
    else:
        init_kwargs = kwargs.get("kwargs", {})

    init_kwargs.update(datadog_kwargs)

    if kwargs_in_args:
        args = tuple(args[:-1]) + (init_kwargs,)
    else:
        kwargs["kwargs"] = init_kwargs

    wrapped(*args, **kwargs)


def _patch_process_run(wrapped, instance, args, kwargs):
    # access private property of Process instance where keyword arguments are
    # stored
    instance_kwargs = getattr(instance, "_kwargs", {})

    # retrieve pin and context attached to process
    pin = instance_kwargs.get(DATADOG_PIN)
    ctx = instance_kwargs.get(DATADOG_CONTEXT)
    if pin is None or ctx is None:
        return wrapped(*args, **kwargs)

    pin.tracer.context_provider.activate(ctx)

    try:
        # replace instance kwargs within a try:finally to ensure original kwargs
        # are re-attached to instance
        new_kwargs = {k: v for (k, v) in instance_kwargs.items() if k not in (DATADOG_PIN, DATADOG_CONTEXT)}
        setattr(instance, "_kwargs", new_kwargs)
        with pin.tracer.trace(
            "multiprocessing.run", service=pin.service, resource=func_name(wrapped), span_type=SpanTypes.WORKER
        ):
            return wrapped(*args, **kwargs)
    finally:
        setattr(instance, "_kwargs", instance_kwargs)
