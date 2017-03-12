from nose.tools import eq_, ok_
from tornado.testing import AsyncHTTPTestCase

from ddtrace.contrib.tornado import trace_app, untrace_app

from . import web
from ...test_tracer import get_dummy_tracer


class TestAppSafety(AsyncHTTPTestCase):
    """
    Ensure that the application patch has the proper safety guards.
    """
    def get_app(self):
        # create a dummy tracer and a Tornado web application
        self.app = web.make_app()
        self.tracer = get_dummy_tracer()
        return self.app

    def tearDown(self):
        super(TestAppSafety, self).tearDown()
        # reset the application if traced
        untrace_app(self.app)

    def test_trace_untrace_app(self):
        # the application must not be traced if untrace_app is called
        trace_app(self.app, self.tracer)
        untrace_app(self.app)

        response = self.fetch('/success/')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(0, len(traces))

    def test_trace_untrace_not_traced(self):
        # the untrace must be safe if the app is not traced
        untrace_app(self.app)

        response = self.fetch('/success/')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(0, len(traces))

    def test_trace_app_twice(self):
        # the application must not be traced twice
        trace_app(self.app, self.tracer)
        trace_app(self.app, self.tracer)

        response = self.fetch('/success/')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))


class TestCustomAppSafety(AsyncHTTPTestCase):
    """
    Ensure that the application patch has the proper safety guards,
    even for custom default handlers.
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
        return self.app

    def tearDown(self):
        super(TestCustomAppSafety, self).tearDown()
        # reset the application if traced
        untrace_app(self.app)

    def test_trace_untrace_app(self):
        # the application must not be traced if untrace_app is called
        trace_app(self.app, self.tracer)
        untrace_app(self.app)

        response = self.fetch('/custom_handler/')
        eq_(400, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(0, len(traces))
