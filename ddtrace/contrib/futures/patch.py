from concurrent import futures

from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ..trace_utils import unwrap as _u
from .threading import _wrap_submit


def patch():
    """Enables Context Propagation between threads"""
    if getattr(futures, "__datadog_patch", False):
        return
    setattr(futures, "__datadog_patch", True)

    _w("concurrent.futures", "ThreadPoolExecutor.submit", _wrap_submit)


def unpatch():
    """Disables Context Propagation between threads"""
    if not getattr(futures, "__datadog_patch", False):
        return
    setattr(futures, "__datadog_patch", False)

    _u(futures.ThreadPoolExecutor, "submit")
