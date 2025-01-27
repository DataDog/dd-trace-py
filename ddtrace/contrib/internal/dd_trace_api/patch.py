from sys import addaudithook
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import dd_trace_api

import ddtrace


_DD_HOOK_PREFIX = "dd.hooks."
_STATE = {"tracer": ddtrace.tracer}
SELF_KEY = "stub_self"
REAL_SPAN_KEY = "real_span"


def _proxy_span_arguments(args: List, kwargs: Dict) -> Tuple[List, Dict]:
    """Convert all dd_trace_api.Span objects in the args/kwargs collections to their held ddtrace.Span objects"""
    proxied_args = []
    for arg in args:
        if isinstance(arg, dd_trace_api.Span):
            proxied_args.append(arg._real_span)
        else:
            proxied_args.append(arg)
    proxied_kwargs = {}
    for name, kwarg in kwargs.items():
        if isinstance(kwarg, dd_trace_api.Span):
            proxied_kwargs[name] = kwarg._real_span
        else:
            proxied_kwargs[name] = kwarg
    return proxied_args, proxied_kwargs


def _patched(method_of, fn_name, store_return: Optional[str] = None):
    def _inner(state_shared_with_api, *args, **kwargs):
        stub_shares_selfref = SELF_KEY in state_shared_with_api
        if stub_shares_selfref:
            operand = getattr(state_shared_with_api[SELF_KEY], "_" + method_of)
        else:
            operand = _STATE[method_of]
        args, kwargs = _proxy_span_arguments(args, kwargs)
        retval = getattr(operand, fn_name)(*args, **kwargs)
        if store_return:
            if store_return in _STATE:
                _STATE[store_return] = retval
            else:
                state_shared_with_api[store_return] = retval

    return _inner


_HANDLERS = {
    "Tracer.flush": None,
    "Tracer.shutdown": None,
    "Tracer.set_tags": None,
    "Tracer.start_span": _patched("tracer", "start_span", store_return=REAL_SPAN_KEY),
    "Tracer.trace": _patched("tracer", "trace", store_return=REAL_SPAN_KEY),
    "Tracer.current_span": _patched("tracer", "current_span", store_return=REAL_SPAN_KEY),
    "Tracer.current_root_span": _patched("tracer", "current_root_span", store_return=REAL_SPAN_KEY),
    "Span.__enter__": None,
    "Span.__exit__": None,
    "Span.finish": None,
    "Span.finish_with_ancestors": None,
    "Span.set_exc_info": None,
    "Span.set_link": None,
    "Span.set_traceback": None,
    "Span.link_span": None,
    "Span.set_tags": None,
    "Span.set_tag": None,
    "Span.set_tag_str": None,
    "Span.set_struct_tag": None,
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
        state_shared_with_api, args = args[0], args[1:]
        handler = _HANDLERS[name_suffix]
        if handler is None:
            handler = _patched(*(REAL_SPAN_KEY if a == "Span" else a.lower() for a in name_suffix.split(".")))
        handler(state_shared_with_api, *args, **kwargs)


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
