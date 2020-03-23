import contextlib
import os

from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.ext import http


def assert_is_measured(span):
    """Assert that the span has the proper _dd.measured tag set"""
    assert SPAN_MEASURED_KEY in span.metrics
    assert SPAN_MEASURED_KEY not in span.meta
    assert span.get_metric(SPAN_MEASURED_KEY) == 1


def assert_is_not_measured(span):
    """Assert that the span does not set _dd.measured"""
    assert SPAN_MEASURED_KEY not in span.meta
    if SPAN_MEASURED_KEY in span.metrics:
        assert span.get_metric(SPAN_MEASURED_KEY) == 0
    else:
        assert SPAN_MEASURED_KEY not in span.metrics


def assert_span_http_status_code(span, code):
    """Assert on the span's 'http.status_code' tag"""
    tag = span.get_tag(http.STATUS_CODE)
    code = str(code)
    assert tag == code, "%r != %r" % (tag, code)


@contextlib.contextmanager
def override_env(env):
    """
    Temporarily override ``os.environ`` with provided values::

        >>> with self.override_env(dict(DATADOG_TRACE_DEBUG=True)):
            # Your test
    """
    # Copy the full original environment
    original = dict(os.environ)

    # Update based on the passed in arguments
    os.environ.update(env)
    try:
        yield
    finally:
        # Full clear the environment out and reset back to the original
        os.environ.clear()
        os.environ.update(original)
