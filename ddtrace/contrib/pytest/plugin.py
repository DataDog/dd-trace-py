from doctest import DocTest
import json
import re
from typing import Dict

import pytest

import ddtrace
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.pytest.constants import FRAMEWORK
from ddtrace.contrib.pytest.constants import HELP_MSG
from ddtrace.contrib.pytest.constants import KIND
from ddtrace.contrib.pytest.constants import XFAIL_REASON
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


PATCH_ALL_HELP_MSG = "Call ddtrace.patch_all before running tests."
log = get_logger(__name__)


def encode_test_parameter(parameter):
    param_repr = repr(parameter)
    # if the representation includes an id() we'll remove it
    # because it isn't constant across executions
    return re.sub(r" at 0[xX][0-9a-fA-F]+", "", param_repr)


def is_enabled(config):
    """Check if the ddtrace plugin is enabled."""
    return config.getoption("ddtrace") or config.getini("ddtrace")


def _extract_span(item):
    """Extract span from `pytest.Item` instance."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `pytest.Item` instance."""
    setattr(item, "_datadog_span", span)


def _extract_reason(call):
    if call.excinfo is not None:
        return call.excinfo.value


def _get_pytest_command(config):
    """Extract and re-create pytest session command from pytest config."""
    command = "pytest"
    if getattr(config, "invocation_params", None):
        command += " {}".format(" ".join(config.invocation_params.args))
    return command


def _start_test_module_span(item):
    """
    Starts a test module span at the start of a new pytest test package.
    Note that ``item`` is a ``pytest.Function`` object referencing the test function being run, so we need to go up
    to find the ``pytest.Package`` ``item``.
    """
    test_session_span = _extract_span(item.session)
    test_module_span = _CIVisibility._instance.tracer._start_span(
        "pytest.test_module",
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        activate=True,
        child_of=test_session_span,
    )
    test_module_span.set_tag_str(COMPONENT, "pytest")
    test_module_span.set_tag_str(SPAN_KIND, KIND)
    test_module_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_module_span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)
    test_module_span.set_tag_str(test.COMMAND, _get_pytest_command(item.config))
    test_module_span.set_tag_str(_EVENT_TYPE, _MODULE_TYPE)
    test_module_span.set_tag(_SESSION_ID, test_session_span.span_id)
    test_module_span.set_tag(_MODULE_ID, test_module_span.span_id)
    test_module_span.set_tag_str(test.MODULE, item.parent.parent.name)
    test_module_span.set_tag_str(test.MODULE_PATH, item.nodeid.rpartition("/")[0])
    _store_span(item.parent.parent, test_module_span)


def _start_test_suite_span(item):
    """
    Starts a test suite span at the start of a new pytest test module.
    Note that ``item`` is a ``pytest.Function`` object referencing the test function being run, so we need to go up
    to find the ``pytest.Module`` ``item``.
    """
    test_session_span = _extract_span(item.session)
    parent_span = test_session_span
    if isinstance(item.parent.parent, pytest.Package):
        test_module_span = _extract_span(item.parent.parent)
        parent_span = test_module_span
    test_suite_span = _CIVisibility._instance.tracer._start_span(
        "pytest.test_suite",
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        activate=True,
        child_of=parent_span,
    )
    test_suite_span.set_tag_str(COMPONENT, "pytest")
    test_suite_span.set_tag_str(SPAN_KIND, KIND)
    test_suite_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_suite_span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)
    test_suite_span.set_tag_str(test.COMMAND, _get_pytest_command(item.config))
    test_suite_span.set_tag_str(_EVENT_TYPE, _SUITE_TYPE)
    test_suite_span.set_tag(_SESSION_ID, test_session_span.span_id)
    test_suite_span.set_tag(_SUITE_ID, test_suite_span.span_id)
    if isinstance(item.parent.parent, pytest.Package):
        test_module_span = _extract_span(item.parent.parent)
        test_suite_span.set_tag(_MODULE_ID, test_module_span.span_id)
        test_suite_span.set_tag_str(test.MODULE, item.parent.parent.name)
    test_suite_span.set_tag_str(test.SUITE, item.parent.module.__name__)
    _store_span(item.parent, test_suite_span)
    return test_suite_span


def pytest_addoption(parser):
    """Add ddtrace options."""
    group = parser.getgroup("ddtrace")

    group._addoption(
        "--ddtrace",
        action="store_true",
        dest="ddtrace",
        default=False,
        help=HELP_MSG,
    )

    group._addoption(
        "--ddtrace-patch-all",
        action="store_true",
        dest="ddtrace-patch-all",
        default=False,
        help=PATCH_ALL_HELP_MSG,
    )

    parser.addini("ddtrace", HELP_MSG, type="bool")
    parser.addini("ddtrace-patch-all", PATCH_ALL_HELP_MSG, type="bool")


def pytest_configure(config):
    config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")
    if is_enabled(config):
        _CIVisibility.enable(config=ddtrace.config.pytest)


def pytest_sessionstart(session):
    if _CIVisibility.enabled:
        test_session_span = _CIVisibility._instance.tracer.trace(
            "pytest.test_session",
            service=_CIVisibility._instance._service,
            span_type=SpanTypes.TEST,
        )
        test_session_span.set_tag_str(COMPONENT, "pytest")
        test_session_span.set_tag_str(SPAN_KIND, KIND)
        test_session_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
        test_session_span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)
        test_session_span.set_tag_str(_EVENT_TYPE, _SESSION_TYPE)
        test_session_span.set_tag_str(test.COMMAND, _get_pytest_command(session.config))
        test_session_span.set_tag(_SESSION_ID, test_session_span.span_id)
        _store_span(session, test_session_span)


def pytest_sessionfinish(session, exitstatus):
    if _CIVisibility.enabled:
        test_session_span = _extract_span(session)
        if test_session_span is not None:
            test_session_span.finish()
        _CIVisibility.disable()


@pytest.fixture(scope="function")
def ddspan(request):
    if _CIVisibility.enabled:
        return _extract_span(request.node)


@pytest.fixture(scope="session")
def ddtracer():
    if _CIVisibility.enabled:
        return _CIVisibility._instance.tracer
    return ddtrace.tracer


@pytest.fixture(scope="session", autouse=True)
def patch_all(request):
    if request.config.getoption("ddtrace-patch-all") or request.config.getini("ddtrace-patch-all"):
        ddtrace.patch_all()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if not _CIVisibility.enabled:
        yield
        return

    if isinstance(item.parent.parent, pytest.Package):
        if _extract_span(item.parent.parent) is None:
            _start_test_module_span(item)

    if _extract_span(item.parent) is None:
        test_suite_span = _start_test_suite_span(item)
        if _CIVisibility.test_skipping_enabled:

            if _CIVisibility.should_skip(
                item.name, test_suite_span.get_tag(test.SUITE), test_suite_span.get_tag(test.MODULE)
            ):
                pytest.skip("Skipped by Datadog Intelligent Test Runner")
                yield
                return

    with _CIVisibility._instance.tracer._start_span(
        ddtrace.config.pytest.operation_name,
        service=_CIVisibility._instance._service,
        resource=item.nodeid,
        span_type=SpanTypes.TEST,
        activate=True,
    ) as span:
        span.set_tag_str(COMPONENT, "pytest")
        span.set_tag_str(SPAN_KIND, KIND)
        span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
        span.set_tag_str(_EVENT_TYPE, SpanTypes.TEST)
        span.set_tag_str(test.NAME, item.name)
        test_session_span = _extract_span(item.session)
        span.set_tag(_SESSION_ID, test_session_span.span_id)
        test_suite_span = _extract_span(item.parent)
        span.set_tag(_SUITE_ID, test_suite_span.span_id)

        if isinstance(item.parent.parent, pytest.Package):
            test_module_span = _extract_span(item.parent.parent)
            span.set_tag(_MODULE_ID, test_module_span.span_id)

        if hasattr(item, "module"):
            span.set_tag_str(test.SUITE, item.module.__name__)
        elif hasattr(item, "dtest") and isinstance(item.dtest, DocTest):
            span.set_tag_str(test.SUITE, item.dtest.globs["__name__"])
        span.set_tag_str(test.TYPE, SpanTypes.TEST)
        span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)

        if item.location and item.location[0]:
            _CIVisibility.set_codeowners_of(item.location[0], span=span)

        # We preemptively set FAIL as a status, because if pytest_runtest_makereport is not called
        # (where the actual test status is set), it means there was a pytest error
        span.set_tag_str(test.STATUS, test.Status.FAIL.value)

        # Parameterized test cases will have a `callspec` attribute attached to the pytest Item object.
        # Pytest docs: https://docs.pytest.org/en/6.2.x/reference.html#pytest.Function
        if getattr(item, "callspec", None):
            parameters = {"arguments": {}, "metadata": {}}  # type: Dict[str, Dict[str, str]]
            for param_name, param_val in item.callspec.params.items():
                try:
                    parameters["arguments"][param_name] = encode_test_parameter(param_val)
                except Exception:
                    parameters["arguments"][param_name] = "Could not encode"
                    log.warning("Failed to encode %r", param_name, exc_info=True)
            span.set_tag_str(test.PARAMETERS, json.dumps(parameters))

        markers = [marker.kwargs for marker in item.iter_markers(name="dd_tags")]
        for tags in markers:
            span.set_tags(tags)
        _store_span(item, span)

        yield

    test_suite_span = _extract_span(item.parent)
    if test_suite_span is not None:
        if nextitem is None or nextitem.parent != item.parent and not test_suite_span.finished:
            test_suite_span.finish()

    if isinstance(item.parent.parent, pytest.Package):
        test_module_span = _extract_span(item.parent.parent)
        if test_module_span is not None:
            if nextitem is None or nextitem.parent.parent != item.parent.parent and not test_module_span.finished:
                test_module_span.finish()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store outcome for tracing."""
    outcome = yield

    if not _CIVisibility.enabled:
        return

    span = _extract_span(item)
    if span is None:
        return

    is_setup_or_teardown = call.when == "setup" or call.when == "teardown"
    has_exception = call.excinfo is not None

    if is_setup_or_teardown and not has_exception:
        return

    result = outcome.get_result()
    xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
    has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

    # If run with --runxfail flag, tests behave as if they were not marked with xfail,
    # that's why no XFAIL_REASON or test.RESULT tags will be added.
    if result.skipped:
        if xfail and not has_skip_keyword:
            # XFail tests that fail are recorded skipped by pytest, should be passed instead
            span.set_tag_str(test.STATUS, test.Status.PASS.value)
            if not item.config.option.runxfail:
                span.set_tag_str(test.RESULT, test.Status.XFAIL.value)
                span.set_tag_str(XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
        else:
            span.set_tag_str(test.STATUS, test.Status.SKIP.value)
        reason = _extract_reason(call)
        if reason is not None:
            span.set_tag(test.SKIP_REASON, reason)
    elif result.passed:
        span.set_tag_str(test.STATUS, test.Status.PASS.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=False) are recorded passed by pytest
            span.set_tag_str(XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
            span.set_tag_str(test.RESULT, test.Status.XPASS.value)
    else:
        span.set_tag_str(test.STATUS, test.Status.FAIL.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
            span.set_tag_str(XFAIL_REASON, getattr(result, "longrepr", "XFail"))
            span.set_tag_str(test.RESULT, test.Status.XPASS.value)
        if call.excinfo:
            span.set_exc_info(call.excinfo.type, call.excinfo.value, call.excinfo.tb)
