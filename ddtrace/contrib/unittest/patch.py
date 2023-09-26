import inspect
import os
import unittest

import wrapt

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


# unittest default settings
config._add(
    "unittest",
    dict(
        _default_service="unittest",
        operation_name=os.getenv("DD_UNITTEST_OPERATION_NAME", default="unittest.test"),
    ),
)


def get_version():
    # type: () -> str
    return ""


def _set_tracer(tracer):
    unittest._datadog_tracer = tracer


def _store_span(item, span):
    """Store span at `unittest` instance."""
    item._datadog_span = span


def _extract_span(item):
    """Extract span from `unittest` instance."""
    return getattr(item, "_datadog_span", None)


def _extract_test_method_name(item):
    """Extract test method name from `unittest` instance."""
    return getattr(item, "_testMethodName", None)


def _extract_suite_name_from_test_method(item):
    item_type = type(item)
    return getattr(item_type, "__name__", None)


def _extract_class_hierarchy_name(item):
    item_type = type(item)
    return getattr(item_type, "__name__", None)


def _extract_module_name_from_test_method(item):
    return getattr(item, "__module__", None)


def _extract_test_skip_reason(item):
    return item[1]


def _extract_test_file_name(item):
    return os.path.basename(inspect.getfile(item.__class__))


def is_unittest_support_enabled():
    return unittest and getattr(unittest, "_datadog_patch", False) and _CIVisibility.enabled


def _is_valid_result(instance, args):
    return instance and type(instance) == unittest.runner.TextTestResult and args


def patch():
    """
    Patch the instrumented methods from unittest
    """
    if (
        not config._ci_visibility_unittest_enabled
        or getattr(unittest, "_datadog_patch", False)
        or _CIVisibility.enabled
    ):
        return

    _CIVisibility.enable(config=ddtrace.config.unittest)

    unittest._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w(unittest, "TextTestResult.addSuccess", add_success_test_wrapper)
    _w(unittest, "TextTestResult.addFailure", add_failure_test_wrapper)
    _w(unittest, "TextTestResult.addError", add_failure_test_wrapper)
    _w(unittest, "TextTestResult.addSkip", add_skip_test_wrapper)

    _w(unittest, "TestCase.run", start_test_wrapper_unittest)


def unpatch():
    if not getattr(unittest, "_datadog_patch", False):
        return

    _u(unittest.TextTestResult, "addSuccess")
    _u(unittest.TextTestResult, "addFailure")
    _u(unittest.TextTestResult, "addError")
    _u(unittest.TextTestResult, "addSkip")
    _u(unittest.TestCase, "run")

    unittest._datadog_patch = False
    _CIVisibility.disable()


def _set_test_span_status(test_item, status, reason=None):
    span = _extract_span(test_item)
    if not span:
        return
    span.set_tag_str(test.STATUS, status)
    if status == test.Status.FAIL.value:
        exc_info = reason
        span.set_exc_info(exc_info[0], exc_info[1], exc_info[2])
    elif status == test.Status.SKIP.value:
        span.set_tag_str(test.SKIP_REASON, reason)


def add_success_test_wrapper(func, instance, args, kwargs):
    if is_unittest_support_enabled() and _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], status=test.Status.PASS.value)

    return func(*args, **kwargs)


def add_failure_test_wrapper(func, instance, args, kwargs):
    if is_unittest_support_enabled() and _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], reason=args[1], status=test.Status.FAIL.value)

    return func(*args, **kwargs)


def add_skip_test_wrapper(func, instance, args, kwargs):
    result = func(*args, **kwargs)
    if is_unittest_support_enabled() and _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], reason=args[1], status=test.Status.SKIP.value)

    return result


def start_test_wrapper_unittest(func, instance, args, kwargs):
    if is_unittest_support_enabled():
        tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
        with tracer.trace(
            ddtrace.config.unittest.operation_name,
            service=_CIVisibility._instance._service,
            resource="unittest.test",
            span_type=SpanTypes.TEST,
        ) as span:
            span.set_tag_str(_EVENT_TYPE, SpanTypes.TEST)

            span.set_tag_str(COMPONENT, COMPONENT_VALUE)
            span.set_tag_str(SPAN_KIND, KIND)

            span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
            span.set_tag_str(test.TYPE, SpanTypes.TEST)
            suite_name = _extract_suite_name_from_test_method(instance)
            span.set_tag_str(test.NAME, _extract_test_method_name(instance))
            span.set_tag_str(test.SUITE, suite_name)
            span.set_tag_str(test.MODULE, _extract_module_name_from_test_method(instance))

            span.set_tag_str(test.STATUS, test.Status.FAIL.value)
            span.set_tag_str(test.CLASS_HIERARCHY, suite_name)
            _CIVisibility.set_codeowners_of(_extract_test_file_name(instance), span=span)

            _store_span(instance, span)
            result = func(*args, **kwargs)
            return result

    return func(*args, **kwargs)
