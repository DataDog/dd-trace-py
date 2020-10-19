import pytest

from ddtrace.ext import SpanTypes, test

from ...pin import Pin

HELP_MSG = "Enable tracing of pytest functions."


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

    if config.getoption("ddtrace", default=config.getini("ddtrace")):
        Pin().onto(config)


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
def pytest_runtest_setup(item):
    pin = Pin.get_from(item.config)
    if pin:
        span = pin.tracer.start_span(SpanTypes.TEST.value, resource=item.nodeid, span_type=SpanTypes.TEST.value)

        tags = [marker.kwargs for marker in item.iter_markers(name="dd_tags")]
        for tag in tags:
            for key, value in tag.items():
                span.set_tag(key, value)

        _store_span(item, span)

    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store outcome for tracing."""
    outcome = yield

    span = _extract_span(item)
    if span is None:
        return

    if (call.when == "call" and span.get_tag(test.STATUS) is None) or (
        call.when == "setup" and call.excinfo is not None
    ):
        try:
            result = outcome.get_result()
            if result.skipped:
                span.set_tag(test.STATUS, test.Status.SKIP.value)
            elif result.passed:
                span.set_tag(test.STATUS, test.Status.PASS.value)
            else:
                raise RuntimeWarning(result)
        except Exception:
            span.set_traceback()
            span.set_tag(test.STATUS, test.Status.FAIL.value)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    yield

    span = _extract_span(item)
    if span:
        span.finish()
