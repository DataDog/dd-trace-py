from unittest import TestCase

import pytest

from ddtrace._trace.filters import FilterRequestsOnUrl
from ddtrace.ext.http import URL
from ddtrace.trace import Span
from ddtrace.trace import TraceFilter


class FilterRequestOnUrlTests(TestCase):
    def test_is_match(self):
        span = Span(name="Name")
        span.set_tag(URL, r"http://example.com")
        filtr = FilterRequestsOnUrl("http://examp.*.com")
        trace = filtr.process_trace([span])
        self.assertIsNone(trace)

    def test_is_not_match(self):
        span = Span(name="Name")
        span.set_tag(URL, r"http://anotherexample.com")
        filtr = FilterRequestsOnUrl("http://examp.*.com")
        trace = filtr.process_trace([span])
        self.assertIsNotNone(trace)

    def test_list_match(self):
        span = Span(name="Name")
        span.set_tag(URL, r"http://anotherdomain.example.com")
        filtr = FilterRequestsOnUrl([r"http://domain\.example\.com", r"http://anotherdomain\.example\.com"])
        trace = filtr.process_trace([span])
        self.assertIsNone(trace)

    def test_list_no_match(self):
        span = Span(name="Name")
        span.set_tag(URL, r"http://cooldomain.example.com")
        filtr = FilterRequestsOnUrl([r"http://domain\.example\.com", r"http://anotherdomain\.example\.com"])
        trace = filtr.process_trace([span])
        self.assertIsNotNone(trace)


def test_not_implemented_trace_filter():
    class Filter(TraceFilter):
        pass

    with pytest.raises(TypeError):
        Filter()
