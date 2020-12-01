import pytest

from ddtrace import config as ddconfig

from ..trace_utils import int_service
from ...constants import SPAN_KIND
from ...ext import SpanTypes, ci, test
from ...pin import Pin
from .constants import FRAMEWORK, HELP_MSG, KIND


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

    parser.addini("ddtrace", HELP_MSG, type="bool")


def pytest_configure(config):
    config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")

    if is_enabled(config):
        Pin(tags=ci.tags(), _config=ddconfig.pytest).onto(config)


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


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    pin = Pin.get_from(item.config)
    if pin is None:
        yield
        return

    with pin.tracer.trace(
        ddconfig.pytest.operation_name,
        service=int_service(pin, ddconfig.pytest),
        resource=item.nodeid,
        span_type=SpanTypes.TEST.value,
    ) as span:
        span.set_tags(pin.tags)
        span.set_tag(SPAN_KIND, KIND)
        span.set_tag(test.FRAMEWORK, FRAMEWORK)
        span.set_tag(test.NAME, item.name)
        span.set_tag(test.SUITE, item.module.__name__)
        span.set_tag(test.TYPE, SpanTypes.TEST.value)

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
        if result.skipped:
            span.set_tag(test.STATUS, test.Status.SKIP.value)
            reason = _extract_reason(call)
            if reason is not None:
                span.set_tag(test.SKIP_REASON, reason)
        elif result.passed:
            span.set_tag(test.STATUS, test.Status.PASS.value)
        else:
            raise RuntimeWarning(result)
    except Exception:
        span.set_traceback()
        span.set_tag(test.STATUS, test.Status.FAIL.value)
