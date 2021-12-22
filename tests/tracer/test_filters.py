from unittest import TestCase

import pytest

from ddtrace.ext.http import URL
from ddtrace.filters import FilterRequestsOnUrl
from ddtrace.filters import TraceCiVisibilityFilter
from ddtrace.filters import TraceFilter
from ddtrace.span import Span


class TraceCiVisibilityFilterTests(TestCase):
    def test_filters_non_test_root_spans(self):
        trace_filter = TraceCiVisibilityFilter()
        root_test_span = Span(tracer=None, name="span1", span_type="test")
        root_test_span._local_root = root_test_span
        # Root span in trace is a test
        trace = [root_test_span]
        self.assertEqual(trace_filter.process_trace(trace), trace)

        root_span = Span(tracer=None, name="span1")
        root_span._local_root = root_span
        # Root span in trace is not a test
        trace = [root_span]
        self.assertEqual(trace_filter.process_trace(trace), None)


class FilterRequestOnUrlTests(TestCase):
    def test_is_match(self):
        span = Span(name="Name", tracer=None)
        span.set_tag(URL, r"http://example.com")
        filtr = FilterRequestsOnUrl("http://examp.*.com")
        trace = filtr.process_trace([span])
        self.assertIsNone(trace)

    def test_is_not_match(self):
        span = Span(name="Name", tracer=None)
        span.set_tag(URL, r"http://anotherexample.com")
        filtr = FilterRequestsOnUrl("http://examp.*.com")
        trace = filtr.process_trace([span])
        self.assertIsNotNone(trace)

    def test_list_match(self):
        span = Span(name="Name", tracer=None)
        span.set_tag(URL, r"http://anotherdomain.example.com")
        filtr = FilterRequestsOnUrl([r"http://domain\.example\.com", r"http://anotherdomain\.example\.com"])
        trace = filtr.process_trace([span])
        self.assertIsNone(trace)

    def test_list_no_match(self):
        span = Span(name="Name", tracer=None)
        span.set_tag(URL, r"http://cooldomain.example.com")
        filtr = FilterRequestsOnUrl([r"http://domain\.example\.com", r"http://anotherdomain\.example\.com"])
        trace = filtr.process_trace([span])
        self.assertIsNotNone(trace)


def test_not_implemented_trace_filter():
    class Filter(TraceFilter):
        pass

    with pytest.raises(TypeError):
        Filter()
