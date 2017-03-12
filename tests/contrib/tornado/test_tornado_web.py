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
        eq_(501, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/http_exception/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('501', request_span.get_tag('http.status_code'))
        eq_('/http_exception/', request_span.get_tag('http.url'))
        eq_(1, request_span.error)
        eq_('HTTP 501: Not Implemented (unavailable)', request_span.get_tag('error.msg'))
        ok_('HTTP 501: Not Implemented (unavailable)' in request_span.get_tag('error.stack'))

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
        eq_('/does_not_exist/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('404', request_span.get_tag('http.status_code'))
        eq_('/does_not_exist/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)

    def test_redirect_handler(self):
        # it should trace the built-in RedirectHandler
        response = self.fetch('/redirect/')
        eq_(200, response.code)

        # we trace two different calls: the RedirectHandler and the SuccessHandler
        traces = self.tracer.writer.pop_traces()
        eq_(2, len(traces))
        eq_(1, len(traces[0]))
        eq_(1, len(traces[1]))

        redirect_span = traces[0][0]
        eq_('tornado-web', redirect_span.service)
        eq_('tornado.request', redirect_span.name)
        eq_('http', redirect_span.span_type)
        eq_('/redirect/', redirect_span.resource)
        eq_('GET', redirect_span.get_tag('http.method'))
        eq_('301', redirect_span.get_tag('http.status_code'))
        eq_('/redirect/', redirect_span.get_tag('http.url'))
        eq_(0, redirect_span.error)

        success_span = traces[1][0]
        eq_('tornado-web', success_span.service)
        eq_('tornado.request', success_span.name)
        eq_('http', success_span.span_type)
        eq_('/success/', success_span.resource)
        eq_('GET', success_span.get_tag('http.method'))
        eq_('200', success_span.get_tag('http.status_code'))
        eq_('/success/', success_span.get_tag('http.url'))
        eq_(0, success_span.error)

    def test_static_handler(self):
        # it should trace the access to static files
        response = self.fetch('/statics/empty.txt')
        eq_(200, response.code)
        eq_('Static file\n', response.body.decode('utf-8'))

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/statics/empty.txt', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/statics/empty.txt', request_span.get_tag('http.url'))
        eq_(0, request_span.error)


class TestCustomTornadoWeb(AsyncHTTPTestCase):
    """
    Ensure that Tornado web handlers are properly traced when using
    a custom default handler.
    """
    def get_app(self):
        # create a dummy tracer and a Tornado web application with
        # a custom default handler
        settings = {
            'default_handler_class': web.CustomDefaultHandler,
            'default_handler_args': dict(status_code=400),
        }

        self.app = web.make_app(settings=settings)
        self.tracer = get_dummy_tracer()
        trace_app(self.app, self.tracer)
        return self.app

    def test_custom_default_handler(self):
        # it should trace any call that uses a custom default handler
        response = self.fetch('/custom_handler/')
        eq_(400, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('/custom_handler/', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('400', request_span.get_tag('http.status_code'))
        eq_('/custom_handler/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)
