from sys import addaudithook

import dd_trace_api

import ddtrace


_DD_HOOK_PREFIX = "dd.hooks."
_CURRENT_SPAN = None
_TRACER = ddtrace.tracer


def _patched_start_span(*args, **kwargs):
    global _CURRENT_SPAN
    _CURRENT_SPAN = _TRACER.start_span(*args, **kwargs)


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
        _HANDLERS[name_suffix](*(args[0][0]), **(args[0][1]))


def get_version() -> str:
    return getattr(dd_trace_api, "__version__", "")


def patch(tracer=None):
    if getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = True
    global _TRACER
    _TRACER = tracer
    addaudithook(_hook)


def unpatch():
    if not getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = False
    # NB sys.addaudithook's cannot be removed
