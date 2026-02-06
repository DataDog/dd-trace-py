from typing import Dict

from wrapt import wrap_function_wrapper as _w


try:
    import azure.durable_functions as durable_functions
except Exception:
    durable_functions = None

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.azure_functions.utils import create_context
from ddtrace.contrib.internal.azure_functions.utils import wrap_function_with_tracing
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind


_DURABLE_ACTIVITY_TRIGGER = "activityTrigger"
_DURABLE_ENTITY_TRIGGER = "entityTrigger"
_DURABLE_ORCHESTRATION_TRIGGER = "orchestrationTrigger"
_DURABLE_ACTIVITY_TRIGGER_NAME = "Activity"
_DURABLE_ENTITY_TRIGGER_NAME = "Entity"
_DURABLE_ACTIVITY_CONTEXT = "azure.durable_functions.patched_activity"
_DURABLE_ENTITY_CONTEXT = "azure.durable_functions.patched_entity"


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("azure-functions-durable")
    except Exception:
        return getattr(durable_functions, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"azure.durable_functions": ">=1.2.1"}


def patch():
    """
    Patch `azure.durable_functions` module for tracing.
    """
    if durable_functions is None:
        return
    if getattr(durable_functions, "_datadog_patch", False):
        return
    durable_functions._datadog_patch = True
    _patch_dfapp()


def _patch_dfapp():
    try:
        from azure.durable_functions.decorators import durable_app
    except Exception:
        return

    if not hasattr(durable_app, "DFApp"):
        return

    from ddtrace.contrib.internal.azure_functions.patch import _patched_get_functions

    Pin().onto(durable_app.DFApp)
    _w("azure.durable_functions", "DFApp.get_functions", _patched_get_functions)


def wrap_durable_functions(pin, functions):
    if durable_functions is None or not getattr(durable_functions, "_datadog_patch", False):
        return

    for function in functions:
        trigger = function.get_trigger()
        if not trigger:
            continue

        trigger_type = trigger.get_binding_name()
        if trigger_type == _DURABLE_ORCHESTRATION_TRIGGER:
            continue

        if trigger_type == _DURABLE_ACTIVITY_TRIGGER:
            trigger_name = _DURABLE_ACTIVITY_TRIGGER_NAME
            context_name = _DURABLE_ACTIVITY_CONTEXT
        elif trigger_type == _DURABLE_ENTITY_TRIGGER:
            trigger_name = _DURABLE_ENTITY_TRIGGER_NAME
            context_name = _DURABLE_ENTITY_CONTEXT
        else:
            continue

        function_name = function.get_function_name()
        func = function.get_user_function()
        function._func = _wrap_durable_trigger(pin, func, function_name, trigger_name, context_name)


def _wrap_durable_trigger(pin, func, function_name, trigger_type, context_name):
    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        return create_context(context_name, pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        return (
            "azure.durable_functions.trigger_call_modifier",
            (ctx, config.azure_functions, function_name, trigger_type, SpanKind.INTERNAL),
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
