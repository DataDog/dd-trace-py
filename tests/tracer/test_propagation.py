from unittest import TestCase

from ddtrace.context import Context
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.http import HTTP_HEADER_ORIGIN
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_SAMPLING_PRIORITY
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from ddtrace.propagation.utils import _CACHE_HEADER_MAX_SIZE
from ddtrace.propagation.utils import _cached_headers
from ddtrace.propagation.utils import from_wsgi_header
from ddtrace.propagation.utils import get_wsgi_header
from tests import DummyTracer


class TestHttpPropagation(TestCase):
    """
    Tests related to the ``Context`` class that hosts the trace for the
    current execution flow.
    """

    def test_inject(self):
        tracer = DummyTracer()

        ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics")
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            headers = {}
            propagator = HTTPPropagator()
            propagator.inject(span.context, headers)

            assert int(headers[HTTP_HEADER_TRACE_ID]) == span.trace_id
            assert int(headers[HTTP_HEADER_PARENT_ID]) == span.span_id
            assert int(headers[HTTP_HEADER_SAMPLING_PRIORITY]) == span.context.sampling_priority
            assert headers[HTTP_HEADER_ORIGIN] == span.context.dd_origin

    def test_extract(self):
        tracer = DummyTracer()

        headers = {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "5678",
            "x-datadog-sampling-priority": "1",
            "x-datadog-origin": "synthetics",
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.parent_id == 5678
            assert span.context.sampling_priority == 1
            assert span.context.dd_origin == "synthetics"

    def test_WSGI_extract(self):
        """Ensure we support the WSGI formatted headers as well."""
        tracer = DummyTracer()

        headers = {
            "HTTP_X_DATADOG_TRACE_ID": "1234",
            "HTTP_X_DATADOG_PARENT_ID": "5678",
            "HTTP_X_DATADOG_SAMPLING_PRIORITY": "1",
            "HTTP_X_DATADOG_ORIGIN": "synthetics",
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.parent_id == 5678
            assert span.context.sampling_priority == 1
            assert span.context.dd_origin == "synthetics"


class TestPropagationUtils(object):
    def test_get_wsgi_header(self):
        assert get_wsgi_header("x-datadog-trace-id") == "HTTP_X_DATADOG_TRACE_ID"

    def test_from_wsgi_header(self):
        assert from_wsgi_header("HTTP_TEST_HEADER") == "Test-Header"
        assert from_wsgi_header("UNHANDLED_HEADER") is None
        assert from_wsgi_header("CONTENT_TYPE") == "Content-Type"

        # Check for the expected cache content
        assert _cached_headers == {
            "HTTP_TEST_HEADER": ("Test-Header", 1),
            "UNHANDLED_HEADER": (None, 1),
            "CONTENT_TYPE": ("Content-Type", 1),
        }

        # Check that the caching is not affecting the returned results
        assert from_wsgi_header("HTTP_TEST_HEADER") == "Test-Header"
        assert from_wsgi_header("UNHANDLED_HEADER") is None
        assert from_wsgi_header("CONTENT_TYPE") == "Content-Type"

        # Check that the cache is not growing indefinitely
        _cached_headers.clear()

        for i in range(_CACHE_HEADER_MAX_SIZE >> 1):
            from_wsgi_header("HTTP_TEST_HEADER" + str(i))

        for i in range(_CACHE_HEADER_MAX_SIZE):
            from_wsgi_header("HTTP_TEST_HEADER" + str(i))

        MAX_TEXT_HEADER = "HTTP_TEST_HEADER" + str(_CACHE_HEADER_MAX_SIZE - 1)
        assert _cached_headers["HTTP_TEST_HEADER0"] == ("Test-Header0", 2)
        assert _cached_headers[MAX_TEXT_HEADER] == ("Test-Header" + str(_CACHE_HEADER_MAX_SIZE - 1), 1)
        assert len(_cached_headers) == _CACHE_HEADER_MAX_SIZE

        from_wsgi_header("HTTP_LAST_DROP")
        assert len(_cached_headers) == (_CACHE_HEADER_MAX_SIZE >> 1) + 1
        assert MAX_TEXT_HEADER not in _cached_headers
        assert "HTTP_LAST_DROP" in _cached_headers
