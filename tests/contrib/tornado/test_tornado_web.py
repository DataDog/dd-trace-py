from nose.tools import eq_, ok_
from tornado.testing import AsyncHTTPTestCase

from ddtrace.contrib.tornado import trace_app

from . import web
from ...test_tracer import get_dummy_tracer


class TestTornadoWeb(AsyncHTTPTestCase):
    """
    Ensure that Tornado web handlers are properly traced.
    """
    def get_app(self):
        # create a dummy tracer and a Tornado web application
        self.app = web.make_app()
        self.tracer = get_dummy_tracer()
        trace_app(self.app, self.tracer)
        return self.app

    def test_success_handler(self):
        # it should trace a handler that returns 200
        response = self.fetch('/success/')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/success/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/success/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)

    def test_nested_handler(self):
        # it should trace a handler that calls the tracer.trace() method
        # using the automatic Context retrieval
        response = self.fetch('/nested/')
        eq_(200, response.code)
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        # check request span
        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/nested/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/nested/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)
        # check nested span
        nested_span = traces[0][1]
        eq_('tornado-web', nested_span.service)
        eq_('tornado.sleep', nested_span.name)
        eq_(0, nested_span.error)
        # check durations because of the yield sleep
        ok_(request_span.duration >= 0.05)
        ok_(nested_span.duration >= 0.05)

    def test_nested_wrap_handler(self):
        # it should trace a handler that calls a coroutine that is
        # wrapped using tracer.wrap() decorator
        response = self.fetch('/nested_wrap/')
        eq_(200, response.code)
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        # check request span
        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/nested_wrap/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/nested_wrap/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)
        # check nested span
        nested_span = traces[0][1]
        eq_('tornado-web', nested_span.service)
        eq_('tornado.coro', nested_span.name)
        eq_(0, nested_span.error)
        # check durations because of the yield sleep
        ok_(request_span.duration >= 0.05)
        ok_(nested_span.duration >= 0.05)

    def test_exception_handler(self):
        # it should trace a handler that raises an exception
        response = self.fetch('/exception/')
        eq_(500, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/exception/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('500', request_span.get_tag('http.status_code'))
        eq_('/exception/', request_span.get_tag('http.url'))
        eq_(1, request_span.error)
        eq_('Ouch!', request_span.get_tag('error.msg'))
        ok_('Exception: Ouch!' in request_span.get_tag('error.stack'))

    def test_http_exception_handler(self):
        # it should trace a handler that raises a Tornado HTTPError
        response = self.fetch('/http_exception/')
        eq_(410, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/http_exception/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('410', request_span.get_tag('http.status_code'))
        eq_('/http_exception/', request_span.get_tag('http.url'))
        eq_(1, request_span.error)
        eq_('HTTP 410: No reason (Gone)', request_span.get_tag('error.msg'))
        ok_('HTTP 410: No reason (Gone)' in request_span.get_tag('error.stack'))

    def test_sync_success_handler(self):
        # it should trace a synchronous handler that returns 200
        response = self.fetch('/sync_success/')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/sync_success/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/sync_success/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)

    def test_sync_exception_handler(self):
        # it should trace a handler that raises an exception
        response = self.fetch('/sync_exception/')
        eq_(500, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/sync_exception/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('500', request_span.get_tag('http.status_code'))
        eq_('/sync_exception/', request_span.get_tag('http.url'))
        eq_(1, request_span.error)
        eq_('Ouch!', request_span.get_tag('error.msg'))
        ok_('Exception: Ouch!' in request_span.get_tag('error.stack'))

    def test_404_handler(self):
        # it should trace 404
        response = self.fetch('/does_not_exist/')
        eq_(404, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/success/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/success/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)
