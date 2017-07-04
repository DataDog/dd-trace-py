from unittest import TestCase

from ddtrace.processors import FilterRequestsOnUrl
from ddtrace.span import Span
from ddtrace.ext.http import URL

class FilterRequestOnUrlTests(TestCase):
    def test_is_match(self):
        span = Span(name='Name', tracer=None)
        span.set_tag(URL, r'http://example.com')
        processor = FilterRequestsOnUrl('http://examp.*.com')
        trace = processor.process_trace([span])
        self.assertIsNone(trace)

    def test_is_not_match(self):
        span = Span(name='Name', tracer=None)
        span.set_tag(URL, r'http://anotherexample.com')
        processor = FilterRequestsOnUrl('http://examp.*.com')
        trace = processor.process_trace([span])
        self.assertIsNotNone(trace)

    def test_list_match(self):
        span = Span(name='Name', tracer=None)
        span.set_tag(URL, r'http://anotherdomain.example.com')
        processor = FilterRequestsOnUrl(['http://domain\.example\.com', 'http://anotherdomain\.example\.com'])
        trace = processor.process_trace([span])
        self.assertIsNone(trace)

    def test_list_no_match(self):
        span = Span(name='Name', tracer=None)
        span.set_tag(URL, r'http://cooldomain.example.com')
        processor = FilterRequestsOnUrl(['http://domain\.example\.com', 'http://anotherdomain\.example\.com'])
        trace = processor.process_trace([span])
        self.assertIsNotNone(trace)
