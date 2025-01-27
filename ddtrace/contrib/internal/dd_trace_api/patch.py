from sys import addaudithook
from typing import Optional

import dd_trace_api

import ddtrace


_DD_HOOK_PREFIX = "dd.hooks."
_STATE = {"tracer": ddtrace.tracer}
SELF_KEY = "stub_self"


def _generic_patched(method_of, fn_name, store_return: Optional[str] = None):
    def _inner(shared_state, *args, **kwargs):
        if SELF_KEY in shared_state:  # XXX
            operand = getattr(shared_state[SELF_KEY], "_" + method_of)
        else:
            operand = _STATE[method_of]
        retval = getattr(operand, fn_name)(*args, **kwargs)
        if store_return:
            shared_state[store_return] = retval
            _STATE[store_return] = retval

    return _inner


_HANDLERS = {
    "Tracer.flush": _generic_patched("tracer", "flush"),
    "Tracer.set_tags": _generic_patched("tracer", "set_tags"),
    "Tracer.start_span": _generic_patched("tracer", "start_span", store_return="real_span"),
    "Tracer.trace": _generic_patched("tracer", "trace", store_return="real_span"),
    "Tracer.current_span": _generic_patched("tracer", "current_span", store_return="real_span"),
    "Tracer.current_root_span": _generic_patched("tracer", "current_root_span", store_return="real_span"),
    "Span.__enter__": _generic_patched("real_span", "__enter__"),
    "Span.__exit__": _generic_patched("real_span", "__exit__"),
    "Span.finish": _generic_patched("real_span", "finish"),
}


def _hook(name, hook_args):
    if not dd_trace_api.__datadog_patch:
        return
    if name.startswith(_DD_HOOK_PREFIX):
        name_suffix = ".".join(name.split(".")[2:])
        if name_suffix not in _HANDLERS:
            return
        kwargs = hook_args[0][1]
        args = hook_args[0][0]
        shared_state, args = args[0], args[1:]
        _HANDLERS[name_suffix](shared_state, *args, **kwargs)


def get_version() -> str:
    return getattr(dd_trace_api, "__version__", "")


def patch(tracer=None):
    if getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = True
    _STATE["tracer"] = tracer
    if not getattr(dd_trace_api, "__dd_has_audit_hook", False):
        addaudithook(_hook)
    dd_trace_api.__dd_has_audit_hook = True


def unpatch():
    if not getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = False
    # NB sys.addaudithook's cannot be removed
