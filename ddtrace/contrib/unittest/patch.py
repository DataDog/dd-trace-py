import inspect
import os
import unittest

import ddtrace
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.unittest.constants import COMPONENT_VALUE
from ddtrace.contrib.unittest.constants import FRAMEWORK
from ddtrace.contrib.unittest.constants import KIND
from ddtrace.ext import SpanTypes
from ddtrace.ext import test
from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
from ddtrace.internal.ci_visibility.constants import EVENT_TYPE as _EVENT_TYPE
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.vendor import wrapt

# unittest default settings
config._add(
    "unittest",
    dict(
        _default_service="unittest",
        operation_name=os.getenv("DD_UNITTEST_OPERATION_NAME", default="unittest.test"),
    ),
)


def _set_tracer(tracer):
    setattr(unittest, "_datadog_tracer", tracer)


def _store_span(item, span):
    """Store span at `unittest` instance."""
    setattr(item, "_datadog_span", span)


def _extract_span(item):
    """Extract span from `unittest` instance."""
    return getattr(item, "_datadog_span", None)


def _extract_test_method_name(item):
    """Extract test method name from `unittest` instance."""
    return getattr(item, "_testMethodName", None)


def _extract_suite_name_from_test_method(item):
    item_type = type(item)
    return getattr(item_type, "__name__", None)


def _extract_module_name_from_test_method(item):
    return getattr(item, "__module__", None)


def _extract_test_skip_reason(args):
    return args[1]


def _extract_test_file_name(item):
    return os.path.basename(inspect.getfile(item.__class__))


def _is_unittest_support_enabled():
    return unittest and getattr(unittest, "_datadog_patch", False) and _CIVisibility.enabled


def patch():
    """
    Patched the instrumented methods from unittest
    """
    if getattr(unittest, "_datadog_patch", False):
        return

    if not _CIVisibility.enabled:
        _CIVisibility.enable(config=ddtrace.config.unittest)

    setattr(unittest, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w(unittest, "TextTestResult.addSuccess", add_success_test_wrapper)
    _w(unittest, "TextTestResult.addFailure", add_failure_test_wrapper)
    _w(unittest, "TextTestResult.addError", add_error_test_wrapper)
    _w(unittest, "TextTestResult.addSkip", add_skip_test_wrapper)

    _w(unittest, "TestCase.run", start_test_wrapper_unittest)
    _w(unittest, "TestSuite.run", start_test_suite_wrapper_unittest)


def unpatch():
    if not getattr(unittest, "_datadog_patch", False):
        return

    setattr(unittest, "_datadog_patch", False)

    _u(unittest, "TextTestResult.addSuccess")
    _u(unittest, "TextTestResult.addFailure")
    _u(unittest, "TextTestResult.addError")
    _u(unittest, "TextTestResult.addSkip")
    _u(unittest, "TestCase.run")


def add_success_test_wrapper(func, instance, args, kwargs):
    if _is_unittest_support_enabled() and instance and type(instance) == unittest.runner.TextTestResult and args:
        test_item = args[0]
        span = _extract_span(test_item)
        if span:
            span.set_tag_str(test.STATUS, test.Status.PASS.value)

    return func(*args, **kwargs)


def add_failure_test_wrapper(func, instance, args, kwargs):
    if _is_unittest_support_enabled() and instance and type(instance) == unittest.runner.TextTestResult and args:
        test_item = args[0]
        span = _extract_span(test_item)
        if span:
            span.set_tag_str(test.STATUS, test.Status.FAIL.value)
        if len(args) > 1:
            exc_info = args[1]
            span.set_exc_info(exc_info[0], exc_info[1], exc_info[2])

    return func(*args, **kwargs)


def add_error_test_wrapper(func, instance, args, kwargs):
    if _is_unittest_support_enabled() and instance and type(instance) == unittest.runner.TextTestResult and args:
        test_item = args[0]
        span = _extract_span(test_item)
        if span:
            span.set_tag_str(test.STATUS, test.Status.FAIL.value)

    return func(*args, **kwargs)


def add_skip_test_wrapper(func, instance, args, kwargs):
    result = func(*args, **kwargs)
    if _is_unittest_support_enabled() and instance and type(instance) == unittest.runner.TextTestResult and args:
        test_item = args[0]
        span = _extract_span(test_item)
        if span:
            span.set_tag_str(test.STATUS, test.Status.SKIP.value)
            span.set_tag_str(test.SKIP_REASON, _extract_test_skip_reason(args))

    return result


def start_test_wrapper_unittest(func, instance, args, kwargs):
    if _is_unittest_support_enabled():
        tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
        with tracer._start_span(
                ddtrace.config.unittest.operation_name,
                service=_CIVisibility._instance._service,
                resource="unittest.test",
                span_type=SpanTypes.TEST
        ) as span:
            span.set_tag_str(_EVENT_TYPE, SpanTypes.TEST)

            span.set_tag_str(COMPONENT, COMPONENT_VALUE)
            span.set_tag_str(SPAN_KIND, KIND)

            span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
            span.set_tag_str(test.TYPE, SpanTypes.TEST)

            span.set_tag_str(test.NAME, _extract_test_method_name(instance))
            span.set_tag_str(test.SUITE, _extract_suite_name_from_test_method(instance))
            span.set_tag_str(test.MODULE, _extract_module_name_from_test_method(instance))

            span.set_tag_str(test.STATUS, test.Status.FAIL.value)

            _CIVisibility.set_codeowners_of(_extract_test_file_name(instance), span=span)

            _store_span(instance, span)
    result = func(*args, **kwargs)

    return result

def start_test_suite_wrapper_unittest(func, instance, args, kwargs):
    result = func(*args, **kwargs)

    return result
