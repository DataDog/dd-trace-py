from sys import addaudithook
from typing import Dict
from typing import List
from typing import Tuple
import weakref

import dd_trace_api

import ddtrace


_DD_HOOK_PREFIX = "dd.hooks."
_TRACER_KEY = "Tracer"
_STUB_TO_REAL = weakref.WeakKeyDictionary()
_STUB_TO_REAL[dd_trace_api.tracer] = ddtrace.tracer


def _proxy_span_arguments(args: List, kwargs: Dict) -> Tuple[List, Dict]:
    """Convert all dd_trace_api.Span objects in the args/kwargs collections to their held ddtrace.Span objects"""
    proxied_args = []
    for arg in args:
        if isinstance(arg, dd_trace_api.Span):
            proxied_args.append(_STUB_TO_REAL[arg])
        else:
            proxied_args.append(arg)
    proxied_kwargs = {}
    for name, kwarg in kwargs.items():
        if isinstance(kwarg, dd_trace_api.Span):
            proxied_kwargs[name] = _STUB_TO_REAL[kwarg]
        else:
            proxied_kwargs[name] = kwarg
    return proxied_args, proxied_kwargs


def _patched(method_of, fn_name):
    def _inner(state_shared_with_api, *args, **kwargs):
        retval_from_api = state_shared_with_api.get("api_return_value")
        operand_stub = state_shared_with_api.get("stub_self")
        args, kwargs = _proxy_span_arguments(args, kwargs)
        retval_from_impl = getattr(_STUB_TO_REAL[operand_stub], fn_name)(*args, **kwargs)
        if "impl_return_value" in state_shared_with_api:
            state_shared_with_api["impl_return_value"] = retval_from_impl
        if retval_from_api is not None:
            _STUB_TO_REAL[retval_from_api] = retval_from_impl

    return _inner


def _hook(name, hook_args):
    if not dd_trace_api.__datadog_patch or not name.startswith(_DD_HOOK_PREFIX):
        return
    args = hook_args[0][0]
    _patched(*(name.replace(_DD_HOOK_PREFIX, "").split(".")))(args[0], *args[1:], **hook_args[0][1])


def get_version() -> str:
    return getattr(dd_trace_api, "__version__", "")


def patch(tracer=None):
    if getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = True
    _STUB_TO_REAL[dd_trace_api.tracer] = tracer
    if not getattr(dd_trace_api, "__dd_has_audit_hook", False):
        addaudithook(_hook)
    dd_trace_api.__dd_has_audit_hook = True


def unpatch():
    if not getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = False
    # NB sys.addaudithook's cannot be removed
