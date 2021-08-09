import json
from typing import Dict

import pytest

import ddtrace

from ...constants import SPAN_KIND
from ...ext import SpanTypes
from ...ext import ci
from ...ext import test
from ...internal.logger import get_logger
from ...pin import Pin
from ..trace_utils import int_service
from .constants import FRAMEWORK
from .constants import HELP_MSG
from .constants import KIND


PATCH_ALL_HELP_MSG = "Call ddtrace.patch_all before running tests."
log = get_logger(__name__)


def is_enabled(config):
    """Check if the ddtrace plugin is enabled."""
    return config.getoption("ddtrace") or config.getini("ddtrace")


def _extract_span(item):
    """Extract span from `pytest.Item` instance."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `pytest.Item` instance."""
    setattr(item, "_datadog_span", span)


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
        Pin(tags=ci.tags(), _config=ddtrace.config.pytest).onto(config)


def pytest_sessionfinish(session, exitstatus):
    """Flush open tracer."""
    pin = Pin.get_from(session.config)
    if pin is not None:
        pin.tracer.shutdown()


@pytest.fixture(scope="function")
def ddspan(request):
    pin = Pin.get_from(request.config)
    if pin:
        return _extract_span(request.node)


@pytest.fixture(scope="session", autouse=True)
def patch_all(request):
    if request.config.getoption("ddtrace-patch-all") or request.config.getini("ddtrace-patch-all"):
        ddtrace.patch_all()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    pin = Pin.get_from(item.config)
    if pin is None:
        yield
        return

    with pin.tracer.trace(
        ddtrace.config.pytest.operation_name,
        service=int_service(pin, ddtrace.config.pytest),
        resource=item.nodeid,
        span_type=SpanTypes.TEST.value,
    ) as span:
        span.set_tags(pin.tags)
        span.set_tag(SPAN_KIND, KIND)
        span.set_tag(test.FRAMEWORK, FRAMEWORK)
        span.set_tag(test.NAME, item.name)
        span.set_tag(test.SUITE, item.module.__name__)
        span.set_tag(test.TYPE, SpanTypes.TEST.value)

        # Parameterized test cases will have a `callspec` attribute attached to the pytest Item object.
        # Pytest docs: https://docs.pytest.org/en/6.2.x/reference.html#pytest.Function
        if getattr(item, "callspec", None):
            parameters = {"arguments": {}, "metadata": {}}  # type: Dict[str, Dict[str, str]]
            for param_name, param_val in item.callspec.params.items():
                try:
                    parameters["arguments"][param_name] = repr(param_val)
                except Exception:
                    parameters["arguments"][param_name] = "Could not encode"
                    log.warning("Failed to encode %r", param_name, exc_info=True)
            span.set_tag(test.PARAMETERS, json.dumps(parameters))

        markers = [marker.kwargs for marker in item.iter_markers(name="dd_tags")]
        for tags in markers:
            span.set_tags(tags)
        _store_span(item, span)

        yield


def _extract_reason(call):
    if call.excinfo is not None:
        return call.excinfo.value


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store outcome for tracing."""
    outcome = yield

    span = _extract_span(item)
    if span is None:
        return

    called_without_status = call.when == "call" and span.get_tag(test.STATUS) is None
    failed_setup = call.when == "setup" and call.excinfo is not None
    if not called_without_status and not failed_setup:
        return

    try:
        result = outcome.get_result()
        xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
        has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

        if result.skipped:
            if xfail and not has_skip_keyword:
                # XFail tests that fail are recorded skipped by pytest, should be passed instead
                span.set_tag(test.RESULT, test.Status.XFAIL.value)
                span.set_tag(test.XFAIL_REASON, result.wasxfail)
                span.set_tag(test.STATUS, test.Status.PASS.value)
            else:
                span.set_tag(test.STATUS, test.Status.SKIP.value)
            reason = _extract_reason(call)
            if reason is not None:
                span.set_tag(test.SKIP_REASON, reason)
        elif result.passed:
            span.set_tag(test.STATUS, test.Status.PASS.value)
            if xfail and not has_skip_keyword:
                # XPass (strict=False) are recorded passed by pytest
                span.set_tag(test.XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
                span.set_tag(test.RESULT, test.Status.XPASS.value)
        else:
            if xfail and not has_skip_keyword:
                # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
                span.set_tag(test.XFAIL_REASON, result.longrepr)
                span.set_tag(test.RESULT, test.Status.XPASS.value)
            raise RuntimeWarning(result)
    except Exception:
        span.set_traceback()
        span.set_tag(test.STATUS, test.Status.FAIL.value)
