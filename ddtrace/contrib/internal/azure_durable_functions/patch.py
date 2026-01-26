from typing import Dict

try:
    import azure.durable_functions as durable_functions
except Exception:
    durable_functions = None
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.internal.schema import schematize_service_name

from .utils import create_context
from .utils import wrap_function_with_tracing


_DURABLE_ACTIVITY_TRIGGER = "activityTrigger"
_DURABLE_ENTITY_TRIGGER = "entityTrigger"
_DURABLE_ORCHESTRATION_TRIGGER = "orchestrationTrigger"
_DURABLE_TRIGGER_DEFS = {
    _DURABLE_ACTIVITY_TRIGGER: ("Activity", "azure.durable_functions.patched_activity"),
    _DURABLE_ENTITY_TRIGGER: ("Entity", "azure.durable_functions.patched_entity"),
}


config._add(
    "azure_durable_functions",
    dict(
        _default_service=schematize_service_name("azure_durable_functions"),
    ),
)


def _mark_wrapped(obj):
    target = getattr(obj, "__wrapped__", None)
    if target is None:
        return
    try:
        target.__dd_wrapped__ = True
    except Exception:
        pass


def _clear_wrapped(obj):
    try:
        if hasattr(obj, "__dd_wrapped__"):
            delattr(obj, "__dd_wrapped__")
    except Exception:
        pass


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("azure-functions-durable")
    except Exception:
        return getattr(durable_functions, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"azure.durable_functions": "*"}


def patch():
    """
    Patch `azure.durable_functions` module for tracing
    """
    if durable_functions is None:
        return
    if getattr(durable_functions, "_datadog_patch", False):
        return
    durable_functions._datadog_patch = True

    try:
        from azure.durable_functions.decorators import durable_app
    except Exception:
        durable_app = None

    if durable_app is not None and hasattr(durable_app, "DFApp"):
        Pin().onto(durable_app.DFApp)
        _w("azure.durable_functions.decorators.durable_app", "DFApp.get_functions", _patched_get_functions)
        _mark_wrapped(durable_app.DFApp.get_functions)


def _patched_get_functions(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    functions = wrapped(*args, **kwargs)

    for function in functions:
        trigger = function.get_trigger()
        if not trigger:
            continue

        trigger_type = trigger.get_binding_name()
        if trigger_type == _DURABLE_ORCHESTRATION_TRIGGER:
            continue

        trigger_def = _DURABLE_TRIGGER_DEFS.get(trigger_type)
        if trigger_def is None:
            continue

        function_name = function.get_function_name()
        func = function.get_user_function()
        trigger_name, context_name = trigger_def
        function._func = _wrap_durable_trigger(pin, func, function_name, trigger_name, context_name)

    return functions


def _wrap_durable_trigger(pin, func, function_name, trigger_type, context_name):
    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        return create_context(context_name, pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        return (
            "azure.durable_functions.trigger_call_modifier",
            (ctx, config.azure_durable_functions, function_name, trigger_type, SpanKind.INTERNAL),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def unpatch():
    if durable_functions is None:
        return
    if not getattr(durable_functions, "_datadog_patch", False):
        return
    durable_functions._datadog_patch = False

    try:
        from azure.durable_functions.decorators import durable_app
    except Exception:
        durable_app = None

    if durable_app is not None and hasattr(durable_app, "DFApp"):
        _u(durable_app.DFApp, "get_functions")
        _clear_wrapped(durable_app.DFApp.get_functions)
