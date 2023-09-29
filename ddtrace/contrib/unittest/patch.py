import inspect
import os
from typing import Union
import unittest

import wrapt

import ddtrace
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.unittest.constants import COMPONENT_VALUE
from ddtrace.contrib.unittest.constants import FRAMEWORK
from ddtrace.contrib.unittest.constants import KIND
from ddtrace.contrib.unittest.constants import MODULE_OPERATION_NAME
from ddtrace.contrib.unittest.constants import SESSION_OPERATION_NAME
from ddtrace.contrib.unittest.constants import SUITE_OPERATION_NAME
from ddtrace.ext import SpanTypes
from ddtrace.ext import test
from ddtrace.ext.ci import RUNTIME_VERSION
from ddtrace.ext.ci import _get_runtime_and_os_metadata
from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
from ddtrace.internal.ci_visibility.constants import EVENT_TYPE as _EVENT_TYPE
from ddtrace.internal.ci_visibility.constants import MODULE_ID as _MODULE_ID
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE as _MODULE_TYPE
from ddtrace.internal.ci_visibility.constants import SESSION_ID as _SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE as _SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE_ID as _SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE as _SUITE_TYPE
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


log = get_logger(__name__)

# unittest default settings
config._add(
    "unittest",
    dict(
        _default_service="unittest",
        operation_name=os.getenv("DD_UNITTEST_OPERATION_NAME", default="unittest.test"),
        strict_naming=asbool(os.getenv("DD_CIVISIBILITY_UNITTEST_STRICT_NAMING", default=True)),
    ),
)


def get_version():
    # type: () -> str
    return ""


def _set_tracer(tracer: ddtrace.tracer):
    """Manually sets the tracer instance to `unittest.`"""
    unittest._datadog_tracer = tracer


def _store_test_span(item, span: ddtrace.Span):
    """Store datadog span at `unittest` test instance."""
    item._datadog_span = span


def _store_module_identifier(test_object: unittest.TextTestRunner):
    """Store module identifier at `unittest` module instance, this is useful to classify event types."""
    if hasattr(test_object, "test") and hasattr(test_object.test, "_tests"):
        for module in test_object.test._tests:
            if len(module._tests) and _extract_module_name_from_module(module):
                _set_identifier(module, "module")


def _store_suite_identifier(module):
    """Store suite identifier at `unittest` suite instance, this is useful to classify event types."""
    if hasattr(module, "_tests"):
        for suite in module._tests:
            if len(suite._tests) and _extract_module_name_from_module(suite):
                _set_identifier(suite, "suite")


def _is_test_module(item) -> bool:
    return hasattr(item, "_datadog_object") and item._datadog_object == "module"


def _is_test_suite(item) -> bool:
    return hasattr(item, "_datadog_object") and item._datadog_object == "suite"


def _is_test(item) -> bool:
    if (
        type(item) == unittest.TestSuite
        or not hasattr(item, "_testMethodName")
        or (ddtrace.config.unittest.strict_naming and not item._testMethodName.startswith("test"))
    ):
        return False
    return True


def _extract_span(item) -> Union[ddtrace.Span, None]:
    return getattr(item, "_datadog_span", None)


def _extract_command_name_from_session(session: unittest.TextTestRunner) -> str:
    if not hasattr(session, "progName"):
        return "python -m unittest"
    return getattr(session, "progName", "")


def _extract_test_method_name(test_object) -> str:
    """Extract test method name from `unittest` instance."""
    return getattr(test_object, "_testMethodName", "")


def _extract_suite_name(item) -> str:
    return type(item._tests[0]).__name__


def _extract_session_span() -> Union[ddtrace.Span, None]:
    return getattr(_CIVisibility, "_datadog_session_span", None)


def _extract_module_span(module_identifier: str) -> Union[ddtrace.Span, None]:
    if hasattr(_CIVisibility, "_unittest_data") and module_identifier in _CIVisibility._unittest_data["modules"]:
        return _CIVisibility._unittest_data["modules"][module_identifier].get("module_span")
    return None


def _extract_suite_span(suite_identifier: str) -> Union[ddtrace.Span, None]:
    if hasattr(_CIVisibility, "_unittest_data") and suite_identifier in _CIVisibility._unittest_data["suites"]:
        return _CIVisibility._unittest_data["suites"][suite_identifier].get("suite_span")
    return None


def _update_status_item(item: ddtrace.Span, status: str):
    """
    Sets the status for each Span implementing the test FAIL logic override.
    """
    existing_status = item.get_tag(test.STATUS)
    if existing_status and (status == test.Status.SKIP.value or existing_status == test.Status.FAIL.value):
        return None
    item.set_tag_str(test.STATUS, status)
    return None


def _extract_suite_name_from_test_method(item) -> str:
    item_type = type(item)
    return getattr(item_type, "__name__", "")


def _extract_module_name_from_test_method(item) -> str:
    return getattr(item, "__module__", "")


def _extract_module_name_from_module(item) -> str:
    if _is_test(item):
        return type(item).__module__
    return ""


def _extract_test_reason(item: tuple) -> str:
    """
    Given a tuple of type [test_class, str], it returns the test failure/skip reason
    """
    return item[1]


def _extract_test_file_name(item) -> str:
    return os.path.basename(inspect.getfile(item.__class__))


def _extract_module_file_path(item) -> str:
    if _is_test(item):
        return os.path.relpath(inspect.getfile(item.__class__))

    return ""


def _generate_test_resource(suite_name: str, test_name: str) -> str:
    return "{}.{}".format(suite_name, test_name)


def _generate_suite_resource(test_suite: str) -> str:
    return "{}".format(test_suite)


def _generate_module_resource(test_module: str) -> str:
    return "{}".format(test_module)


def _generate_session_resource(test_command: str) -> str:
    return "{}".format(test_command)


def _set_identifier(item, name: str):
    """
    Adds an event type classification to a `unittest` test.
    """
    item._datadog_object = name


def _is_valid_result(instance: unittest.TextTestRunner, args: tuple) -> bool:
    return instance and isinstance(instance, unittest.runner.TextTestResult) and args


def _is_valid_test_call(kwargs: dict) -> bool:
    """
    Validates that kwargs is empty to ensure that `unittest` is running a test
    """
    return not len(kwargs)


def _is_valid_module_suite_call(func) -> bool:
    """
    Validates that the mocked function is an actual function from `unittest`
    """
    return type(func).__name__ == "method" or type(func).__name__ == "instancemethod"


def _is_invoked_by_cli(instance: unittest.TextTestRunner) -> bool:
    return (
        hasattr(instance, "progName")
        or hasattr(_CIVisibility, "_datadog_entry")
        and _CIVisibility._datadog_entry == "cli"
    )


def _is_invoked_by_text_test_runner() -> bool:
    return hasattr(_CIVisibility, "_datadog_entry") and _CIVisibility._datadog_entry == "TextTestRunner"


def _generate_module_suite_path(test_module_path: str, test_suite_name: str) -> str:
    return "{}.{}".format(test_module_path, test_suite_name)


def _populate_suites_and_modules(test_objects: list, seen_suites: dict, seen_modules: dict):
    """
    Discovers suites and modules and initializes the seen_suites and seen_modules dictionaries.
    """
    if not hasattr(test_objects, "__iter__"):
        return
    for test_object in test_objects:
        if not _is_test(test_object):
            _populate_suites_and_modules(test_object, seen_suites, seen_modules)
            continue
        test_module_path = _extract_module_file_path(test_object)
        test_suite_name = _extract_suite_name_from_test_method(test_object)
        test_module_suite_path = _generate_module_suite_path(test_module_path, test_suite_name)
        if test_module_path not in seen_modules:
            seen_modules[test_module_path] = {
                "module_span": None,
                "remaining_suites": 0,
            }
        if test_module_suite_path not in seen_suites:
            seen_suites[test_module_suite_path] = {
                "suite_span": None,
                "remaining_tests": 0,
            }

            seen_modules[test_module_path]["remaining_suites"] += 1

        seen_suites[test_module_suite_path]["remaining_tests"] += 1


def _finish_remaining_suites_and_modules(seen_suites: dict, seen_modules: dict):
    """
    Forces all suite and module spans to finish and updates their statuses.
    """
    for suite in seen_suites.values():
        if suite["suite_span"] and not suite["suite_span"].finished:
            _update_status_item(suite["suite_span"]._parent, suite["suite_span"].get_tag(test.STATUS))
            suite["suite_span"].finish()

    for module in seen_modules.values():
        if module["module_span"] and not module["module_span"].finished:
            _update_status_item(module["module_span"]._parent, module["module_span"].get_tag(test.STATUS))
            module["module_span"].finish()
    del _CIVisibility._unittest_data


def _update_remaining_suites_and_modules(
    test_module_suite_path: str, test_module_path: str, test_module_span: ddtrace.Span, test_suite_span: ddtrace.Span
):
    """
    Updates the remaining test suite and test counter and finishes spans when these have finished their execution.
    """
    suite_dict = _CIVisibility._unittest_data["suites"][test_module_suite_path]
    modules_dict = _CIVisibility._unittest_data["modules"][test_module_path]

    suite_dict["remaining_tests"] -= 1
    if suite_dict["remaining_tests"] == 0:
        modules_dict["remaining_suites"] -= 1
        _finish_span(test_suite_span)
    if modules_dict["remaining_suites"] == 0:
        _finish_span(test_module_span)


def patch():
    """
    Patch the instrumented methods from unittest
    """
    if getattr(unittest, "_datadog_patch", False) or _CIVisibility.enabled:
        return

    _CIVisibility.enable(config=ddtrace.config.unittest)

    unittest._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w(unittest, "TextTestResult.addSuccess", add_success_test_wrapper)
    _w(unittest, "TextTestResult.addFailure", add_failure_test_wrapper)
    _w(unittest, "TextTestResult.addError", add_failure_test_wrapper)
    _w(unittest, "TextTestResult.addSkip", add_skip_test_wrapper)
    _w(unittest, "TextTestResult.addExpectedFailure", add_xfail_test_wrapper)
    _w(unittest, "TextTestResult.addUnexpectedSuccess", add_xpass_test_wrapper)
    _w(unittest, "TestCase.run", handle_test_wrapper)
    _w(unittest, "TestSuite.run", collect_text_test_runner_session)
    _w(unittest, "TextTestRunner.run", handle_text_test_runner_wrapper)
    _w(unittest, "TestProgram.runTests", handle_cli_run)


def unpatch():
    """
    Undo patched instrumented methods from unittest
    """
    if not getattr(unittest, "_datadog_patch", False):
        return

    _u(unittest.TextTestResult, "addSuccess")
    _u(unittest.TextTestResult, "addFailure")
    _u(unittest.TextTestResult, "addError")
    _u(unittest.TextTestResult, "addSkip")
    _u(unittest.TextTestResult, "addExpectedFailure")
    _u(unittest.TextTestResult, "addUnexpectedSuccess")
    _u(unittest.TestSuite, "run")
    _u(unittest.TestCase, "run")
    _u(unittest.TextTestRunner, "run")
    _u(unittest.TestProgram, "runTests")

    unittest._datadog_patch = False
    _CIVisibility.disable()


def _set_test_span_status(test_item, status: str, exc_info: str = None, skip_reason: str = None):
    span = _extract_span(test_item)
    if not span:
        log.debug("Tried setting test result for test but could not find span for %s", test_item)
        return None
    span.set_tag_str(test.STATUS, status)
    if exc_info:
        span.set_exc_info(exc_info[0], exc_info[1], exc_info[2])
    if status == test.Status.SKIP.value:
        span.set_tag_str(test.SKIP_REASON, skip_reason)


def _set_test_xpass_xfail_result(test_item, result: str):
    """
    Sets `test.result` and `test.status` to a XFAIL or XPASS test.
    """
    span = _extract_span(test_item)
    if not span:
        log.debug("Tried setting test result for an xpass or xfail test but could not find span for %s", test_item)
        return None
    span.set_tag_str(test.RESULT, result)
    status = span.get_tag(test.STATUS)
    if result == test.Status.XFAIL.value:
        if status == test.Status.PASS.value:
            span.set_tag_str(test.STATUS, test.Status.FAIL.value)
        elif status == test.Status.FAIL.value:
            span.set_tag_str(test.STATUS, test.Status.PASS.value)


def add_success_test_wrapper(func, instance: unittest.TextTestRunner, args: tuple, kwargs: dict):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], status=test.Status.PASS.value)

    return func(*args, **kwargs)


def add_failure_test_wrapper(func, instance: unittest.TextTestRunner, args: tuple, kwargs: dict):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], exc_info=_extract_test_reason(args), status=test.Status.FAIL.value)

    return func(*args, **kwargs)


def add_xfail_test_wrapper(func, instance: unittest.TextTestRunner, args: tuple, kwargs: dict):
    if _is_valid_result(instance, args):
        _set_test_xpass_xfail_result(test_item=args[0], result=test.Status.XFAIL.value)

    return func(*args, **kwargs)


def add_skip_test_wrapper(func, instance: unittest.TextTestRunner, args: tuple, kwargs: dict):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], skip_reason=_extract_test_reason(args), status=test.Status.SKIP.value)

    return func(*args, **kwargs)


def add_xpass_test_wrapper(func, instance, args: tuple, kwargs: dict):
    if _is_valid_result(instance, args):
        _set_test_xpass_xfail_result(test_item=args[0], result=test.Status.XPASS.value)

    return func(*args, **kwargs)


def handle_test_wrapper(func, instance, args: tuple, kwargs: dict):
    """
    Creates module and suite spans for `unittest` test executions.
    """
    if _is_valid_test_call(kwargs) and _is_test(instance) and hasattr(_CIVisibility, "_unittest_data"):
        test_suite_name = _extract_suite_name_from_test_method(instance)
        test_name = _extract_test_method_name(instance)
        test_module_path = _extract_module_file_path(instance)
        test_module_suite_path = _generate_module_suite_path(test_module_path, test_suite_name)
        test_suite_span = _extract_suite_span(test_module_suite_path)
        test_module_span = _extract_module_span(test_module_path)
        if test_module_span is None and test_module_path in _CIVisibility._unittest_data["modules"]:
            test_module_span = _start_test_module_span(instance)
            _CIVisibility._unittest_data["modules"][test_module_path]["module_span"] = test_module_span
        if test_suite_span is None and test_module_suite_path in _CIVisibility._unittest_data["suites"]:
            test_suite_span = _start_test_suite_span(instance)
            _CIVisibility._unittest_data["suites"][test_module_suite_path]["suite_span"] = test_suite_span
        if not test_module_span or not test_suite_span:
            log.debug("Suite and/or module span not found for test: %s", test_name)
            return func(*args, **kwargs)
        with _start_test_span(instance, test_suite_span) as span:
            result = func(*args, **kwargs)
            _update_status_item(test_suite_span, span.get_tag(test.STATUS))
        _update_remaining_suites_and_modules(
            test_module_suite_path, test_module_path, test_module_span, test_suite_span
        )
        return result
    return func(*args, **kwargs)


def collect_text_test_runner_session(func, instance: unittest.TestSuite, args: tuple, kwargs: dict):
    """
    Discovers test suites and tests for the current `unittest` `TextTestRunner` execution
    """
    if not _is_valid_module_suite_call(func):
        return func(*args, **kwargs)
    if not hasattr(_CIVisibility, "_unittest_data"):
        _CIVisibility._unittest_data = {"suites": {}, "modules": {}}
    if _is_invoked_by_text_test_runner():
        seen_suites = _CIVisibility._unittest_data["suites"]
        seen_modules = _CIVisibility._unittest_data["modules"]
        _populate_suites_and_modules(instance._tests, seen_suites, seen_modules)

        result = func(*args, **kwargs)

        return result
    result = func(*args, **kwargs)
    return result


def _start_test_session_span(instance) -> ddtrace.Span:
    """
    Starts a test session span and sets the required tags for a `unittest` session instance.
    """
    tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
    test_command = _extract_command_name_from_session(instance)
    resource_name = _generate_session_resource(test_command)
    test_session_span = tracer.trace(
        SESSION_OPERATION_NAME,
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        resource=resource_name,
    )
    test_session_span.set_tag_str(_EVENT_TYPE, _SESSION_TYPE)
    test_session_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))

    test_session_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
    test_session_span.set_tag_str(SPAN_KIND, KIND)

    test_session_span.set_tag_str(test.COMMAND, test_command)
    test_session_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_session_span.set_tag_str(test.FRAMEWORK_VERSION, _get_runtime_and_os_metadata()[RUNTIME_VERSION])

    test_session_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)

    _store_module_identifier(instance)
    return test_session_span


def _start_test_module_span(instance) -> ddtrace.Span:
    """
    Starts a test module span and sets the required tags for a `unittest` module instance.
    """
    tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
    test_session_span = _extract_session_span()
    test_module_name = _extract_module_name_from_module(instance)
    resource_name = _generate_module_resource(test_module_name)
    test_module_span = tracer._start_span(
        MODULE_OPERATION_NAME,
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        activate=True,
        child_of=test_session_span,
        resource=resource_name,
    )
    test_module_span.set_tag_str(_EVENT_TYPE, _MODULE_TYPE)
    test_module_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
    test_module_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))

    test_module_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
    test_module_span.set_tag_str(SPAN_KIND, KIND)

    test_module_span.set_tag_str(test.COMMAND, test_session_span.get_tag(test.COMMAND))
    test_module_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_module_span.set_tag_str(test.FRAMEWORK_VERSION, _get_runtime_and_os_metadata()[RUNTIME_VERSION])

    test_module_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)
    test_module_span.set_tag_str(test.MODULE, test_module_name)
    test_module_span.set_tag_str(test.MODULE_PATH, _extract_module_file_path(instance))
    _store_suite_identifier(instance)
    return test_module_span


def _start_test_suite_span(instance) -> ddtrace.Span:
    """
    Starts a test suite span and sets the required tags for a `unittest` suite instance.
    """
    tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
    test_module_path = _extract_module_file_path(instance)
    test_module_span = _extract_module_span(test_module_path)
    test_suite_name = _extract_suite_name_from_test_method(instance)
    resource_name = _generate_suite_resource(test_suite_name)
    test_suite_span = tracer._start_span(
        SUITE_OPERATION_NAME,
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        child_of=test_module_span,
        activate=True,
        resource=resource_name,
    )
    test_suite_span.set_tag_str(_EVENT_TYPE, _SUITE_TYPE)
    test_suite_span.set_tag_str(_SESSION_ID, test_module_span.get_tag(_SESSION_ID))
    test_suite_span.set_tag_str(_SUITE_ID, str(test_suite_span.span_id))
    test_suite_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))

    test_suite_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
    test_suite_span.set_tag_str(SPAN_KIND, KIND)

    test_suite_span.set_tag_str(test.COMMAND, test_module_span.get_tag(test.COMMAND))
    test_suite_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_suite_span.set_tag_str(test.FRAMEWORK_VERSION, _get_runtime_and_os_metadata()[RUNTIME_VERSION])

    test_suite_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)
    test_suite_span.set_tag_str(test.SUITE, test_suite_name)
    test_suite_span.set_tag_str(test.MODULE, test_module_span.get_tag(test.MODULE))
    test_suite_span.set_tag_str(test.MODULE_PATH, test_module_path)
    return test_suite_span


def _start_test_span(instance, test_suite_span: ddtrace.Span) -> ddtrace.Span:
    """
    Starts a test  span and sets the required tags for a `unittest` test instance.
    """
    tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
    test_suite_name = _extract_suite_name_from_test_method(instance)
    test_name = _extract_test_method_name(instance)
    resource_name = _generate_test_resource(test_suite_name, test_name)
    span = tracer._start_span(
        ddtrace.config.unittest.operation_name,
        service=_CIVisibility._instance._service,
        resource=resource_name,
        span_type=SpanTypes.TEST,
        child_of=test_suite_span,
        activate=True,
    )
    span.set_tag_str(_EVENT_TYPE, SpanTypes.TEST)
    span.set_tag_str(_SESSION_ID, test_suite_span.get_tag(_SESSION_ID))
    span.set_tag_str(_MODULE_ID, test_suite_span.get_tag(_MODULE_ID))
    span.set_tag_str(_SUITE_ID, test_suite_span.get_tag(_SUITE_ID))

    span.set_tag_str(COMPONENT, COMPONENT_VALUE)
    span.set_tag_str(SPAN_KIND, KIND)

    span.set_tag_str(test.COMMAND, test_suite_span.get_tag(test.COMMAND))
    span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    span.set_tag_str(test.FRAMEWORK_VERSION, _get_runtime_and_os_metadata()[RUNTIME_VERSION])

    span.set_tag_str(test.TYPE, SpanTypes.TEST)
    span.set_tag_str(test.NAME, test_name)
    span.set_tag_str(test.SUITE, test_suite_name)
    span.set_tag_str(test.MODULE, test_suite_span.get_tag(test.MODULE))
    span.set_tag_str(test.MODULE_PATH, test_suite_span.get_tag(test.MODULE_PATH))
    span.set_tag_str(test.STATUS, test.Status.FAIL.value)
    span.set_tag_str(test.CLASS_HIERARCHY, test_suite_name)

    _CIVisibility.set_codeowners_of(_extract_test_file_name(instance), span=span)

    _store_test_span(instance, span)
    return span


def _finish_span(current_span: ddtrace.Span):
    """
    Finishes active span and populates span status upwards
    """
    current_status = current_span.get_tag(test.STATUS)
    parent_span = current_span._parent
    if current_status and parent_span:
        _update_status_item(parent_span, current_status)
    elif not current_status:
        current_span.set_tag_str(test.SUITE, test.Status.FAIL.value)
    current_span.finish()


def handle_cli_run(func, instance: unittest.TestProgram, args: tuple, kwargs: dict):
    """
    Creates session span and discovers test suites and tests for the current `unittest` CLI execution
    """
    test_session_span = None
    if _is_invoked_by_cli(instance):
        if not hasattr(_CIVisibility, "_unittest_data"):
            _CIVisibility._unittest_data = {"suites": {}, "modules": {}}
        for parent_module in instance.test._tests:
            for module in parent_module._tests:
                _populate_suites_and_modules(
                    module, _CIVisibility._unittest_data["suites"], _CIVisibility._unittest_data["modules"]
                )

        test_session_span = _start_test_session_span(instance)
        _CIVisibility._datadog_entry = "cli"
        _CIVisibility._datadog_session_span = test_session_span

    try:
        result = func(*args, **kwargs)
    except SystemExit as e:
        if _CIVisibility.enabled and test_session_span:
            _finish_remaining_suites_and_modules(
                _CIVisibility._unittest_data["suites"], _CIVisibility._unittest_data["modules"]
            )
            _finish_span(test_session_span)
        raise e
    return result


def handle_text_test_runner_wrapper(func, instance: unittest.TextTestResult, args: tuple, kwargs: dict):
    """
    Creates session span if unittest is called through the `TextTestRunner` method
    """
    if _is_invoked_by_cli(instance):
        return func(*args, **kwargs)
    _CIVisibility._datadog_entry = "TextTestRunner"
    if not hasattr(_CIVisibility, "_datadog_session_span"):
        _CIVisibility._datadog_session_span = _start_test_session_span(instance)
        _CIVisibility._datadog_expected_sessions = 0
        _CIVisibility._datadog_finished_sessions = 0
    _CIVisibility._datadog_expected_sessions += 1
    try:
        result = func(*args, **kwargs)
    except SystemExit as e:
        _CIVisibility._datadog_finished_sessions += 1
        if _CIVisibility._datadog_finished_sessions == _CIVisibility._datadog_expected_sessions:
            _finish_remaining_suites_and_modules(
                _CIVisibility._unittest_data["suites"], _CIVisibility._unittest_data["modules"]
            )
            _finish_span(_CIVisibility._datadog_session_span)
            del _CIVisibility._datadog_session_span
        raise e

    _CIVisibility._datadog_finished_sessions += 1
    if _CIVisibility._datadog_finished_sessions == _CIVisibility._datadog_expected_sessions:
        _finish_remaining_suites_and_modules(
            _CIVisibility._unittest_data["suites"], _CIVisibility._unittest_data["modules"]
        )
        _finish_span(_CIVisibility._datadog_session_span)
        del _CIVisibility._datadog_session_span
    return result
