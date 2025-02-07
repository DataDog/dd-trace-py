from sys import addaudithook
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import weakref

import dd_trace_api

import ddtrace


_DD_HOOK_NAME = "dd.hook"
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


def _call_on_real_instance(
    operand_stub: dd_trace_api._Stub, method_name: str, retval_from_api: Optional[Any], *args: List, **kwargs: Dict
) -> None:
    """
    Call `method_name` on the real object corresponding to `operand_stub` with `args` and `kwargs` as arguments.

    Store the value that will be returned from the API call we're in the middle of, for the purpose
    of mapping from those Stub objects to their real counterparts.
    """
    args, kwargs = _proxy_span_arguments(args, kwargs)
    retval_from_impl = getattr(_STUB_TO_REAL[operand_stub], method_name)(*args, **kwargs)
    if retval_from_api is not None:
        _STUB_TO_REAL[retval_from_api] = retval_from_impl


def _hook(name, hook_args):
    """Called in response to `sys.audit` events"""
    if name != _DD_HOOK_NAME or not dd_trace_api.__datadog_patch:
        return
    args = hook_args[0][0]
    api_return_value, stub_self, method_name = args[0:3]
    _call_on_real_instance(stub_self, method_name, api_return_value, *args[3:], **hook_args[0][1])


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
