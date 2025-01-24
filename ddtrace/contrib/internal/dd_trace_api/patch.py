import os
from sys import addaudithook

import dd_trace_api
from wrapt import wrap_function_wrapper as _w

import ddtrace
from ddtrace import config
from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_request
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Pin


_DD_HOOK_PREFIX = "dd.hooks."
_CURRENT_SPAN = None


def _patched_start_span(*args, **kwargs):
    global _CURRENT_SPAN
    _CURRENT_SPAN = dd_trace_api._tracer.start_span(*args, **kwargs)


def _patched_span_enter(*args, **kwargs):
    _CURRENT_SPAN.__enter__(*args, **kwargs)


def _patched_span_exit(*args, **kwargs):
    _CURRENT_SPAN.__exit__(*args, **kwargs)


def _patched_span_finish(*args, **kwargs):
    _CURRENT_SPAN.finish(*args, **kwargs)


_HANDLERS = {
    "Tracer.start_span": _patched_start_span,
    "Span.__enter__": _patched_span_enter,
    "Span.__exit__": _patched_span_exit,
    "Span.finish": _patched_span_finish,
}


def _hook(name, args):
    if not dd_trace_api.__datadog_patch:
        return
    if name.startswith(_DD_HOOK_PREFIX):
        name_suffix = ".".join(name.split(".")[2:])
        if name_suffix not in _HANDLERS:
            return
        print(name_suffix)
        _HANDLERS[name_suffix](*(args[0][0]), **(args[0][1]))


def get_version() -> str:
    return getattr(dd_trace_api, "__version__", "")


def patch(tracer=None):
    if getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = True
    Pin.override(dd_trace_api, _tracer=tracer or ddtrace.tracer)
    addaudithook(_hook)


def unpatch():
    if not getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = False
    # NB sys.addaudithook's cannot be removed
