import inspect
import os
import unittest
import time

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
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE as _MODULE_TYPE
from ddtrace.internal.ci_visibility.constants import MODULE_ID as _MODULE_ID
from ddtrace.internal.ci_visibility.constants import SESSION_ID as _SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE as _SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE as _SUITE_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE_ID as _SUITE_ID
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.vendor import wrapt


log = get_logger(__name__)

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


def _store_session_span(module, span):
    """Store session span at `unittest` module instance"""
    if hasattr(module, "test") and hasattr(module.test, "_tests"):
        for test in module.test._tests:
            setattr(test, "_datadog_session_span", span)


def _store_module_span(suite, span):
    """Store module span at `unittest` suite instance"""
    if hasattr(suite, "_tests"):
        for test in suite._tests:
            setattr(test, "_datadog_module_span", span)


def _store_suite_span(test, span):
    """Store suite span at `unittest` test instance"""
    if hasattr(test, "_tests"):
        for test in test._tests:
            setattr(test, "_datadog_suite_span", span)


def _is_test_suite(item):
    if type(item) == unittest.suite.TestSuite and len(item._tests) and type(item._tests[0]) != unittest.suite.TestSuite:
        return True
    return False


def _is_test_module(item):
    if type(item) == unittest.suite.TestSuite and len(item._tests) and _is_test_suite(item._tests[0]):
        return True
    return False


def _extract_span(item):
    """Extract span from `unittest` instance."""
    return getattr(item, "_datadog_span", None)


def _extract_command_name_from_session(session):
    """Extract command name from `unittest` instance"""
    return getattr(session, "progName", None)


def _extract_test_method_name(test):
    """Extract test method name from `unittest` instance."""
    return getattr(test, "_testMethodName", None)


def _extract_suite_name(item):
    return type(item._tests[0]).__name__


def _extract_session_span(session):
    return getattr(session, "_datadog_session_span", None)


def _extract_module_span(session):
    return getattr(session, "_datadog_module_span", None)


def _extract_suite_span(session):
    return getattr(session, "_datadog_suite_span", None)


def _extract_status_from_result(result):
    if hasattr(result, "errors") and hasattr(result, "failures") and hasattr(
            result, "skipped"):
        if len(result.errors) or len(result.failures):
            return test.Status.FAIL.value
        elif result.testsRun == len(result.skipped):
            return test.Status.SKIP.value
        return test.Status.PASS.value

    return test.Status.FAIL.value


def _extract_status_from_session(session):
    if hasattr(session, "result"):
        return _extract_status_from_result(session.result)
    return test.Status.FAIL.value


def _extract_status_from_module(args):
    if len(args):
        return _extract_status_from_result(args[0])
    return test.Status.FAIL.value


def _extract_suite_name_from_test_method(item):
    item_type = type(item)
    return getattr(item_type, "__name__", None)


def _extract_module_name_from_test_method(item):
    return getattr(item, "__module__", None)


def _extract_module_name_from_module(item):
    return type(item._tests[0]._tests[0]).__module__


def _extract_test_skip_reason(args):
    return args[1]


def _extract_test_file_name(item):
    return os.path.basename(inspect.getfile(item.__class__))


def _extract_module_file_path(item):
    return os.path.relpath(inspect.getfile(item._tests[0]._tests[0].__class__))


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

    _w(unittest, "TestCase.run", handle_test_wrapper)
    _w(unittest, "TestSuite.run", handle_module_suite_wrapper)
    _w(unittest, "TestProgram.runTests", handle_session_wrapper)


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


def handle_test_wrapper(func, instance, args, kwargs):
    if _is_unittest_support_enabled():
        tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
        span = tracer._start_span(
                ddtrace.config.unittest.operation_name,
                service=_CIVisibility._instance._service,
                resource="unittest.test",
                span_type=SpanTypes.TEST
        )
        print('creating span!')
        test_suite_span = _extract_suite_span(instance)
        span.set_tag_str(_EVENT_TYPE, SpanTypes.TEST)
        span.set_tag_str(_SESSION_ID, test_suite_span.get_tag(_SESSION_ID))
        span.set_tag_str(_MODULE_ID, test_suite_span.get_tag(_MODULE_ID))
        span.set_tag_str(_SUITE_ID, test_suite_span.get_tag(_SUITE_ID))

        span.set_tag_str(COMPONENT, COMPONENT_VALUE)
        span.set_tag_str(SPAN_KIND, KIND)

        span.set_tag_str(test.COMMAND, test_suite_span.get_tag(test.COMMAND))
        span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
        span.set_tag_str(test.TYPE, SpanTypes.TEST)

        span.set_tag_str(test.NAME, _extract_test_method_name(instance))
        span.set_tag_str(test.SUITE, test_suite_span.get_tag(test.SUITE))
        span.set_tag_str(test.MODULE, test_suite_span.get_tag(test.MODULE))
        span.set_tag_str(test.MODULE_PATH, test_suite_span.get_tag(test.MODULE_PATH))
        span.set_tag_str(test.STATUS, test.Status.FAIL.value)

        _CIVisibility.set_codeowners_of(_extract_test_file_name(instance), span=span)

        _store_span(instance, span)
        print('sending span waiting!')
        result = func(*args, **kwargs)
        span.finish()
        return result
    result = func(*args, **kwargs)
    return result


def handle_module_suite_wrapper(func, instance, args, kwargs):
    if _is_unittest_support_enabled():
        tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
        if _is_test_suite(instance):
            test_module_span = _extract_module_span(instance)
            test_suite_name = _extract_suite_name(instance)

            test_suite_span = tracer._start_span(
                "unittest.test_suite",
                service=_CIVisibility._instance._service,
                span_type=SpanTypes.TEST,
                activate=True,
                child_of=test_module_span,
            )
            test_suite_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
            test_suite_span.set_tag_str(SPAN_KIND, KIND)
            test_suite_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
            test_suite_span.set_tag_str(test.COMMAND, test_module_span.get_tag(test.COMMAND))
            test_suite_span.set_tag_str(_EVENT_TYPE, _SUITE_TYPE)
            test_suite_span.set_tag_str(_SESSION_ID, test_module_span.get_tag(_SESSION_ID))
            test_suite_span.set_tag_str(_SUITE_ID, str(test_suite_span.span_id))
            test_suite_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
            test_suite_span.set_tag_str(test.MODULE, test_module_span.get_tag(test.MODULE))
            test_module_path = test_module_span.get_tag(test.MODULE_PATH)
            test_suite_span.set_tag_str(test.MODULE_PATH, test_module_path)
            test_suite_span.set_tag_str(test.SUITE, test_suite_name)
            _store_span(instance, test_suite_span)
            _store_suite_span(instance, test_suite_span)
            result = func(*args, **kwargs)
            test_suite_span.finish()
            return result
        elif _is_test_module(instance):
            test_session_span = _extract_session_span(instance)
            test_module_name = _extract_module_name_from_module(instance)
            test_module_span = tracer._start_span(
                "unittest.test_module",
                service=_CIVisibility._instance._service,
                span_type=SpanTypes.TEST,
                activate=True,
                child_of=test_session_span,
            )
            test_module_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
            test_module_span.set_tag_str(SPAN_KIND, KIND)
            test_module_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
            test_module_span.set_tag_str(test.COMMAND, test_session_span.get_tag(test.COMMAND))
            test_module_span.set_tag_str(_EVENT_TYPE, _MODULE_TYPE)
            if test_session_span:
                test_module_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
            test_module_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
            test_module_span.set_tag_str(test.MODULE, test_module_name)
            test_module_span.set_tag_str(test.MODULE_PATH, _extract_module_file_path(instance))
            _store_module_span(instance, test_module_span)
            result = func(*args, **kwargs)
            # TODO: Not working properly as it depends on the previous module due to the args parameter, integrate with suite instead
            test_module_span.set_tag_str(test.STATUS, _extract_status_from_module(args))
            test_module_span.finish()
            return result

    result = func(*args, **kwargs)
    return result


def handle_session_wrapper(func, instance, args, kwargs):
    if _is_unittest_support_enabled():
        tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
        test_session_span = tracer.trace("unittest.test_session", service=_CIVisibility._instance._service,
                                         span_type=SpanTypes.TEST, )
        test_session_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
        test_session_span.set_tag_str(SPAN_KIND, KIND)
        test_session_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
        test_session_span.set_tag_str(_EVENT_TYPE, _SESSION_TYPE)
        test_session_span.set_tag_str(test.COMMAND, _extract_command_name_from_session(instance))
        test_session_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
        _store_span(instance, test_session_span)
        _store_session_span(instance, test_session_span)
    try:
        result = func(*args, **kwargs)
    except SystemExit as e:
        if _CIVisibility.enabled:
            log.debug("CI Visibility enabled - finishing unittest test session")
            test_session_span = _extract_span(instance)
            if test_session_span:
                test_session_span.set_tag_str(test.STATUS, _extract_status_from_session(instance))
                test_session_span.finish()
                _CIVisibility.disable()
        raise e
    return result
