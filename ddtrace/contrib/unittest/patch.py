from ddtrace.vendor import wrapt
from ..trace_utils import unwrap as _u
import unittest

def patch():
    """
    Patched the instrumented methods from unittest
    """
    if getattr(unittest, "_datadog_patch", False):
        return
    setattr(unittest, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w(unittest, "TestResult.startTest", startTestWrapper)


def startTestWrapper(func, instance, args, kwargs):
    print('Wrapped by Datadog!')

    return func(*args, **kwargs)