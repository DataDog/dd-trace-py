from ddtrace.vendor import wrapt
from ..trace_utils import unwrap as _u
import unittest
import ddtrace
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import test
from ddtrace.ext import SpanTypes
from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.constants import EVENT_TYPE as _EVENT_TYPE
from ddtrace.internal.ci_visibility.constants import MODULE_ID as _MODULE_ID
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE as _MODULE_TYPE
from ddtrace.internal.ci_visibility.constants import SESSION_ID as _SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE as _SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE
from ddtrace.internal.ci_visibility.constants import SUITE_ID as _SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE as _SUITE_TYPE
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.constants import COMPONENT

def patch():
    """
    Patched the instrumented methods from unittest
    """
    if getattr(unittest, "_datadog_patch", False):
        return
    setattr(unittest, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w(unittest, "TextTestResult.addSuccess", add_sucess_test_wrapper)
    _w(unittest, "TextTestResult.addFailure", add_failure_test_wrapper)
    _w(unittest, "TestCase.run", start_test_wrapper)
    #_w(unittest, "TestSuite.run", create_test_suite_span)


def add_sucess_test_wrapper(func, instance, args, kwargs):
    """
    if instance and type(instance) == unittest.runner.TextTestResult:
        if args:
            for test in args:
                if test._outcome.success:
                    print(f'Looks like test id: {test._testMethodName} ran succesfully')
    """
    return func(*args, **kwargs)


def add_failure_test_wrapper(func, instance, args, kwargs):
    result = func(*args, **kwargs)
    # Code

    return result


def create_test_suite_span(func, instance, args, kwargs):
    # TODO: research empty test suites
    return func(*args, **kwargs)

def start_test_wrapper(func, instance, args, kwargs):
    if not _CIVisibility.enabled:
        _CIVisibility.enable(config=ddtrace.config.unittest)

    with _CIVisibility._instance.tracer._start_span(
        ddtrace.config.unittest.operation_name,
        service=_CIVisibility._instance._service,
        resource="pytest.test_suite",
        span_type="test",
        activate=True,
    ) as span:
        test_method_name = instance._testMethodName
        span.set_tag_str(COMPONENT, "unittest")
        span.set_tag_str(SPAN_KIND, "test")
        span.set_tag_str(test.FRAMEWORK, "unittest")
        span.set_tag_str(_EVENT_TYPE, SpanTypes.TEST)
        span.set_tag_str(test.NAME, test_method_name)
        span.set_tag_str(test.COMMAND, "unittest")
        #span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))

        #span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
        span.set_tag_str(test.MODULE, "main")
        span.set_tag_str(test.MODULE_PATH, "main.py")

        #span.set_tag_str(_SUITE_ID, str(test_suite_span.span_id))
        span.set_tag_str(test.TYPE, SpanTypes.TEST)
        span.set_tag_str(test.FRAMEWORK_VERSION, "unittest")
        """
        if item.location and item.location[0]:
            _CIVisibility.set_codeowners_of(item.location[0], span=span)
        """
        # We preemptively set FAIL as a status, because if pytest_runtest_makereport is not called
        # (where the actual test status is set), it means there was a pytest error
        span.set_tag_str(test.STATUS, test.Status.FAIL.value)
        old_errors, old_failures, old_skipped, old_tests_run = len(args[0].errors), len(args[0].failures), len(args[0].skipped), args[0].testsRun
        result = func(*args, **kwargs)
        new_errors, new_failures, new_skipped, new_tests_run = len(args[0].errors), len(args[0].failures), len(
            args[0].skipped), args[0].testsRun

        if new_errors != old_errors:
            print(f'Error {test_method_name}')
        elif new_failures != old_failures:
            print(f'Failure {test_method_name}')
        elif new_skipped != old_skipped:
            print(f'Skipped {test_method_name}')
            span.set_tag_str(test.STATUS, test.Status.SKIP.value)
        elif new_tests_run == old_tests_run + 1:
            print(f'It ran OK {test_method_name}')
            span.set_tag_str(test.STATUS, test.Status.PASS.value)
        span.set_tag_str(test.SUITE, 'suite_test')
        span.finish()
    return result

