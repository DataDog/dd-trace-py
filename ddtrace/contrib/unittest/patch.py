import inspect
import os
import platform
import unittest

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


def get_version():
    # type: () -> str
    return ""


def _set_tracer(tracer):
    """Manually sets the tracer instance to `unittest.`"""
    unittest._datadog_tracer = tracer


def _store_test_span(item, span):
    """Store span at `unittest` instance."""
    item._datadog_span = span


def _store_session_span(module, span):
    """Store session span at `unittest` module instance."""
    module._datadog_session_span = span
    if hasattr(module, "test") and hasattr(module.test, "_tests"):
        for submodule in module.test._tests:
            submodule._datadog_session_span = span


def _store_module_span(suite, span):
    """Store module span at `unittest` suite instance."""
    suite._datadog_module_span = span
    if hasattr(suite, "_tests"):
        for subsuite in suite._tests:
            subsuite._datadog_module_span = span


def _store_suite_span(test, span):
    """Store suite span at `unittest` test instance."""
    if hasattr(test, "_tests"):
        for test_object in test._tests:
            test_object._datadog_suite_span = span


def _is_test_suite(item):
    return _extract_module_span(item) and len(item._tests)


def _is_test_module(item):
    return _extract_session_span(item) and len(item._tests) and _extract_module_name_from_module(item)


def _extract_span(item):
    """Extract span from `unittest` instance."""
    return getattr(item, "_datadog_span", None)


def _extract_command_name_from_session(session):
    """Extract command name from `unittest` instance."""
    if not hasattr(session, "progName"):
        return "python -m unittest"
    return getattr(session, "progName", None)


def _extract_test_method_name(test_object):
    """Extract test method name from `unittest` instance."""
    return getattr(test_object, "_testMethodName", None)


def _extract_suite_name(item):
    return type(item._tests[0]).__name__


def _extract_session_span(test_object):
    return getattr(test_object, "_datadog_session_span", None)


def _extract_module_span(test_object, resource_identifier=None):
    if getattr(test_object, "_datadog_module_span", None):
        return getattr(test_object, "_datadog_module_span", None)
    if (
        resource_identifier
        and hasattr(_CIVisibility, "_unittest_data")
        and resource_identifier in _CIVisibility._unittest_data["modules"]
    ):
        return _CIVisibility._unittest_data["modules"][resource_identifier].get("module_span")
    return None


def _extract_suite_span(test_object, resource_identifier=None):
    if getattr(test_object, "_datadog_suite_span", None):
        return getattr(test_object, "_datadog_suite_span", None)
    if (
        resource_identifier
        and hasattr(_CIVisibility, "_unittest_data")
        and resource_identifier in _CIVisibility._unittest_data["suites"]
    ):
        return _CIVisibility._unittest_data["suites"][resource_identifier].get("suite_span")
    return None


def _update_status_item(item, status):
    existing_status = item.get_tag(test.STATUS)
    if (
        not status
        or existing_status
        and (status == test.Status.SKIP.value or existing_status == test.Status.FAIL.value)
    ):
        return
    item.set_tag_str(test.STATUS, status)


def _extract_suite_name_from_test_method(item):
    item_type = type(item)
    return getattr(item_type, "__name__", None)


def _extract_class_hierarchy_name(item):
    item_type = type(item)
    return getattr(item_type, "__name__", None)


def _extract_module_name_from_test_method(item):
    return getattr(item, "__module__", None)


def _extract_module_name_from_module(item):
    if type(item) != unittest.TestSuite:
        return type(item).__module__
    if not len(item._tests):
        return None
    module_name = None
    for suite in item._tests:
        if type(suite) != unittest.TestSuite:
            module_name = type(suite).__module__
            break
        if not len(suite._tests):
            continue
        for test_object in suite._tests:
            module_name = type(test_object).__module__
            if not module_name:
                continue
            break

    return module_name


def _extract_test_reason(item):
    return item[1]


def _extract_test_file_name(item):
    return os.path.basename(inspect.getfile(item.__class__))


def _extract_module_file_path(item):
    if type(item) != unittest.TestSuite:
        return os.path.relpath(inspect.getfile(item.__class__))
    if not len(item._tests):
        return ""
    if type(item._tests[0]) != unittest.TestSuite:
        return os.path.relpath(inspect.getfile(item._tests[0].__class__))
    if not hasattr(item._tests, "_tests") or not len(item._tests._tests):
        return ""

    return os.path.relpath(inspect.getfile(item._tests[0]._tests[0].__class__))


def _generate_test_resource(suite_name, test_name):
    return suite_name + "." + test_name


def _generate_suite_resource(framework_name, test_suite):
    return framework_name + "." + "test_suite" + "." + test_suite


def _generate_module_resource(framework_name, test_module):
    return framework_name + "." + "test_module" + "." + test_module


def _generate_session_resource(framework_name, test_command):
    return framework_name + "." + "test_session" + "." + test_command


def _get_identifier(item):
    return getattr(item, "_datadog_object", None)


def _is_valid_result(instance, args):
    return instance and type(instance) == unittest.runner.TextTestResult and args


def _is_valid_test_call(kwargs):
    return not len(kwargs)


def _is_valid_module_suite_call(func):
    return type(func).__name__ == "method" or type(func).__name__ == "instancemethod"


def _is_invoked_by_cli(args):
    return (
        not args
        or not len(args)
        or not len(args[0]._tests)
        or type(args[0]._tests[0]) == unittest.TestSuite
        and not len(args[0]._tests[0]._tests)
        or hasattr(args[0], "_datadog_entry")
    )


def _is_invoked_by_text_test_runner(instance):
    return hasattr(instance, "_datadog_entry") and instance._datadog_entry == "TextTestRunner"


def _get_python_version():
    return platform.python_version()


def _generate_module_suite_path(test_module_path, test_suite_name):
    return test_module_path + "." + test_suite_name


def _populate_suites_and_modules(test_objects, seen_suites, seen_modules):
    for test_object in test_objects:
        test_module_name = os.path.relpath(inspect.getfile(test_object.__class__))
        test_suite_name = _extract_class_hierarchy_name(test_object)
        module_suite_name = test_module_name + "." + test_suite_name
        if test_module_name not in seen_modules:
            seen_modules[test_module_name] = {
                "module_span": None,
                "expected_suites": 0,
                "ran_suites": 0,
            }
        if module_suite_name not in seen_suites:
            seen_suites[module_suite_name] = {
                "suite_span": None,
                "expected_tests": 0,
                "ran_tests": 0,
            }

            seen_modules[test_module_name]["expected_suites"] += 1

        seen_suites[module_suite_name]["expected_tests"] += 1


def _finish_remaining_suites_and_modules(seen_suites, seen_modules):
    for suite in seen_suites.values():
        if suite["expected_tests"] != suite["ran_tests"] and suite["suite_span"]:
            _update_status_item(suite["suite_span"]._parent, suite["suite_span"].get_tag(test.STATUS))
            suite["suite_span"].finish()

    for module in seen_modules.values():
        if module["expected_suites"] != module["ran_suites"] and module["module_span"]:
            _update_status_item(module["module_span"]._parent, module["module_span"].get_tag(test.STATUS))
            module["module_span"].finish()


def _update_remaining_suites_and_modules(test_module_suite_path, test_module_path, test_module_span, test_suite_span):
    _CIVisibility._unittest_data["suites"][test_module_suite_path]["ran_tests"] += 1
    if (
        _CIVisibility._unittest_data["suites"][test_module_suite_path]["ran_tests"]
        == _CIVisibility._unittest_data["suites"][test_module_suite_path]["expected_tests"]
    ):
        _CIVisibility._unittest_data["modules"][test_module_path]["ran_suites"] += 1
        _update_status_item(test_module_span, test_suite_span.get_tag(test.STATUS))
        test_suite_span.finish()
    if (
        _CIVisibility._unittest_data["modules"][test_module_path]["ran_suites"]
        == _CIVisibility._unittest_data["modules"][test_module_path]["expected_suites"]
    ):
        _finish_test_module_span(test_module_span)


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
    _w(unittest, "TextTestRunner.run", handle_text_test_runner_wrapper)
    _w(unittest, "TestCase.run", handle_test_wrapper)
    _w(unittest, "TestSuite.run", handle_module_suite_wrapper)
    _w(unittest, "TestProgram.runTests", handle_session_wrapper)


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
    _u(unittest.TextTestRunner, "run")
    _u(unittest.TestCase, "run")
    _u(unittest.TestSuite, "run")
    _u(unittest.TestProgram, "runTests")

    unittest._datadog_patch = False
    _CIVisibility.disable()


def _set_test_span_status(test_item, status, exc_info=None, skip_reason=None):
    span = _extract_span(test_item)
    if not span:
        return
    span.set_tag_str(test.STATUS, status)
    if exc_info:
        span.set_exc_info(exc_info[0], exc_info[1], exc_info[2])
    if status == test.Status.SKIP.value:
        span.set_tag_str(test.SKIP_REASON, skip_reason)


def add_success_test_wrapper(func, instance, args, kwargs):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], status=test.Status.PASS.value)

    return func(*args, **kwargs)


def add_failure_test_wrapper(func, instance, args, kwargs):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], exc_info=_extract_test_reason(args), status=test.Status.FAIL.value)

    return func(*args, **kwargs)


def add_xfail_test_wrapper(func, instance, args, kwargs):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], exc_info=_extract_test_reason(args), status=test.Status.XFAIL.value)

    return func(*args, **kwargs)


def add_skip_test_wrapper(func, instance, args, kwargs):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], skip_reason=_extract_test_reason(args), status=test.Status.SKIP.value)

    return func(*args, **kwargs)


def add_xpass_test_wrapper(func, instance, args, kwargs):
    if _is_valid_result(instance, args):
        _set_test_span_status(test_item=args[0], status=test.Status.XPASS.value)

    return func(*args, **kwargs)


def handle_test_wrapper(func, instance, args, kwargs):
    if _is_valid_test_call(kwargs):
        tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
        test_suite_name = _extract_class_hierarchy_name(instance)
        test_name = _extract_test_method_name(instance)
        resource_name = _generate_test_resource(test_suite_name, test_name)
        test_module_path = _extract_module_file_path(instance)
        test_module_suite_path = _generate_module_suite_path(test_module_path, test_suite_name)
        test_suite_span = _extract_suite_span(instance, test_module_suite_path)
        test_module_span = _extract_module_span(instance, test_module_path)
        if hasattr(_CIVisibility, "_unittest_data"):
            if test_module_span is None:
                test_module_name = _extract_module_name_from_module(instance)
                test_session_span = _CIVisibility._datadog_session_span
                resource_name = _generate_module_resource(FRAMEWORK, test_module_name)
                test_module_span = tracer._start_span(
                    MODULE_OPERATION_NAME,
                    service=_CIVisibility._instance._service,
                    span_type=SpanTypes.TEST,
                    activate=True,
                    child_of=test_session_span,
                    resource=resource_name,
                )
                test_module_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
                test_module_span.set_tag_str(SPAN_KIND, KIND)
                test_module_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
                test_module_span.set_tag_str(test.FRAMEWORK_VERSION, _get_python_version())
                test_module_span.set_tag_str(test.COMMAND, test_session_span.get_tag(test.COMMAND))
                test_module_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)
                test_module_span.set_tag_str(_EVENT_TYPE, _MODULE_TYPE)
                test_module_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
                test_module_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
                test_module_span.set_tag_str(test.MODULE, test_module_name)
                test_module_span.set_tag_str(test.MODULE_PATH, test_module_path)
                _CIVisibility._unittest_data["modules"][test_module_path]["module_span"] = test_module_span
            if test_suite_span is None:
                resource_name = _generate_suite_resource(FRAMEWORK, test_suite_name)
                test_suite_span = tracer._start_span(
                    SUITE_OPERATION_NAME,
                    service=_CIVisibility._instance._service,
                    span_type=SpanTypes.TEST,
                    child_of=test_module_span,
                    activate=True,
                    resource=resource_name,
                )
                test_suite_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
                test_suite_span.set_tag_str(SPAN_KIND, KIND)
                test_suite_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
                test_suite_span.set_tag_str(test.FRAMEWORK_VERSION, _get_python_version())
                test_suite_span.set_tag_str(test.COMMAND, test_module_span.get_tag(test.COMMAND))
                test_suite_span.set_tag_str(_EVENT_TYPE, _SUITE_TYPE)
                test_suite_span.set_tag_str(_SESSION_ID, test_module_span.get_tag(_SESSION_ID))
                test_suite_span.set_tag_str(_SUITE_ID, str(test_suite_span.span_id))
                test_suite_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
                test_suite_span.set_tag_str(test.MODULE, test_module_span.get_tag(test.MODULE))
                test_module_path = test_module_span.get_tag(test.MODULE_PATH)
                test_suite_span.set_tag_str(test.MODULE_PATH, test_module_path)
                test_suite_span.set_tag_str(test.SUITE, test_suite_name)
                test_suite_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)
                _CIVisibility._unittest_data["suites"][test_module_suite_path]["suite_span"] = test_suite_span
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
        span.set_tag_str(test.FRAMEWORK_VERSION, _get_python_version())
        span.set_tag_str(test.TYPE, SpanTypes.TEST)

        span.set_tag_str(test.NAME, test_name)
        span.set_tag_str(test.SUITE, test_suite_name)
        span.set_tag_str(test.MODULE, test_suite_span.get_tag(test.MODULE))
        span.set_tag_str(test.MODULE_PATH, test_suite_span.get_tag(test.MODULE_PATH))
        span.set_tag_str(test.STATUS, test.Status.FAIL.value)
        span.set_tag_str(test.CLASS_HIERARCHY, test_suite_name)

        _CIVisibility.set_codeowners_of(_extract_test_file_name(instance), span=span)

        _store_test_span(instance, span)
        result = func(*args, **kwargs)
        _update_status_item(test_suite_span, span.get_tag(test.STATUS))
        span.finish()
        if hasattr(_CIVisibility, "_unittest_data"):
            _update_remaining_suites_and_modules(
                test_module_suite_path, test_module_path, test_module_span, test_suite_span
            )
        return result
    return func(*args, **kwargs)


def handle_module_suite_wrapper(func, instance, args, kwargs):
    if _is_valid_module_suite_call(func):
        tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
        if (
            _is_test_suite(instance)
            or hasattr(instance, "_datadog_entry")
            and instance._datadog_entry == "TextTestRunner"
        ):
            if _is_invoked_by_text_test_runner(instance):
                if not hasattr(_CIVisibility, "_unittest_data"):
                    _CIVisibility._unittest_data = {"suites": {}, "modules": {}}
                seen_suites = _CIVisibility._unittest_data["suites"]
                seen_modules = _CIVisibility._unittest_data["modules"]
                _populate_suites_and_modules(instance._tests, seen_suites, seen_modules)
                result = func(*args, **kwargs)

                _finish_remaining_suites_and_modules(seen_suites, seen_modules)
                return result
            test_module_span = _extract_module_span(instance)
            test_suite_name = _extract_suite_name(instance)
            resource_name = _generate_suite_resource(FRAMEWORK, test_suite_name)
            test_suite_span = tracer._start_span(
                SUITE_OPERATION_NAME,
                service=_CIVisibility._instance._service,
                span_type=SpanTypes.TEST,
                activate=True,
                child_of=test_module_span,
                resource=resource_name,
            )
            test_suite_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
            test_suite_span.set_tag_str(SPAN_KIND, KIND)
            test_suite_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
            test_suite_span.set_tag_str(test.FRAMEWORK_VERSION, _get_python_version())
            test_suite_span.set_tag_str(test.COMMAND, test_module_span.get_tag(test.COMMAND))
            test_suite_span.set_tag_str(_EVENT_TYPE, _SUITE_TYPE)
            test_suite_span.set_tag_str(_SESSION_ID, test_module_span.get_tag(_SESSION_ID))
            test_suite_span.set_tag_str(_SUITE_ID, str(test_suite_span.span_id))
            test_suite_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
            test_suite_span.set_tag_str(test.MODULE, test_module_span.get_tag(test.MODULE))
            test_module_path = test_module_span.get_tag(test.MODULE_PATH)
            test_suite_span.set_tag_str(test.MODULE_PATH, test_module_path)
            test_suite_span.set_tag_str(test.SUITE, test_suite_name)
            test_suite_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)
            _store_test_span(instance, test_suite_span)
            _store_suite_span(instance, test_suite_span)
            result = func(*args, **kwargs)
            _update_status_item(test_module_span, test_suite_span.get_tag(test.STATUS))
            test_suite_span.finish()
            return result
        elif _is_test_module(instance):
            test_module_span = _start_test_module_span(instance)
            result = func(*args, **kwargs)
            _finish_test_module_span(test_module_span)
            return result

    result = func(*args, **kwargs)
    return result


def _start_test_session_span(instance):
    if _get_identifier(instance):
        return None
    tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
    test_command = _extract_command_name_from_session(instance)
    resource_name = _generate_session_resource(FRAMEWORK, test_command)
    test_session_span = tracer.trace(
        SESSION_OPERATION_NAME,
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        resource=resource_name,
    )
    test_session_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)
    test_session_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
    test_session_span.set_tag_str(SPAN_KIND, KIND)
    test_session_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_session_span.set_tag_str(test.FRAMEWORK_VERSION, _get_python_version())
    test_session_span.set_tag_str(_EVENT_TYPE, _SESSION_TYPE)
    test_session_span.set_tag_str(test.COMMAND, test_command)
    test_session_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
    _store_test_span(instance, test_session_span)
    _store_session_span(instance, test_session_span)
    return test_session_span


def _finish_test_session_span(test_session_span):
    log.debug("CI Visibility enabled - finishing unittest test session")
    test_session_span.finish()


def _start_test_module_span(instance):
    tracer = getattr(unittest, "_datadog_tracer", _CIVisibility._instance.tracer)
    test_session_span = _extract_session_span(instance)
    test_module_name = _extract_module_name_from_module(instance)
    resource_name = _generate_module_resource(FRAMEWORK, test_module_name)
    test_module_span = tracer._start_span(
        MODULE_OPERATION_NAME,
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        activate=True,
        child_of=test_session_span,
        resource=resource_name,
    )
    test_module_span.set_tag_str(COMPONENT, COMPONENT_VALUE)
    test_module_span.set_tag_str(SPAN_KIND, KIND)
    test_module_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_module_span.set_tag_str(test.FRAMEWORK_VERSION, _get_python_version())
    test_module_span.set_tag_str(test.COMMAND, test_session_span.get_tag(test.COMMAND))
    test_module_span.set_tag_str(test.TEST_TYPE, SpanTypes.TEST)
    test_module_span.set_tag_str(_EVENT_TYPE, _MODULE_TYPE)
    test_module_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
    test_module_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
    test_module_span.set_tag_str(test.MODULE, test_module_name)
    test_module_span.set_tag_str(test.MODULE_PATH, _extract_module_file_path(instance))
    _store_module_span(instance, test_module_span)
    return test_module_span


def _finish_test_module_span(test_module_span):
    module_status = test_module_span.get_tag(test.STATUS)
    if module_status:
        test_session_span = test_module_span._parent
        _update_status_item(test_session_span, module_status)
    test_module_span.finish()


def handle_session_wrapper(func, instance, args, kwargs):
    test_session_span = None
    if len(instance.test._tests) and not hasattr(instance.test, "_datadog_entry"):
        test_session_span = _start_test_session_span(instance)
        instance.test._datadog_entry = "cli"
    try:
        result = func(*args, **kwargs)
    except SystemExit as e:
        if _CIVisibility.enabled and test_session_span:
            _finish_test_session_span(test_session_span)
            _CIVisibility.disable()
        raise e
    return result


def handle_text_test_runner_wrapper(func, instance, args, kwargs):
    """
    Creates session and module spans if the unittest is called through the `TextTestRunner` method
    """
    if _is_invoked_by_cli(args):
        return func(*args, **kwargs)
    test_object = args[0]
    test_object._datadog_entry = "TextTestRunner"
    try:
        if not hasattr(_CIVisibility, "_datadog_session_span"):
            _CIVisibility._datadog_session_span = _start_test_session_span(instance)
            _CIVisibility._datadog_expected_sessions = 0
            _CIVisibility._datadog_finished_sessions = 0
        _CIVisibility._datadog_expected_sessions += 1
        result = func(*args, **kwargs)
    except Exception as e:
        _CIVisibility._datadog_finished_sessions += 1
        if _CIVisibility._datadog_finished_sessions == _CIVisibility._datadog_expected_sessions:
            _finish_test_session_span(_CIVisibility._datadog_session_span)
        raise e

    _CIVisibility._datadog_finished_sessions += 1
    if _CIVisibility._datadog_finished_sessions == _CIVisibility._datadog_expected_sessions:
        _finish_test_session_span(_CIVisibility._datadog_session_span)
    return result
