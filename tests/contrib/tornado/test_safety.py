import threading

from nose.tools import eq_
from tornado import httpclient
from tornado.testing import AsyncHTTPTestCase, gen_test

from ddtrace.contrib.tornado import trace_app, untrace_app

from . import web
from ...test_tracer import get_dummy_tracer


class TestAsyncConcurrency(AsyncHTTPTestCase):
    """
    Ensure that application instrumentation doesn't break asynchronous concurrency.
    """
    def get_app(self):
        # create a dummy tracer and a Tornado web application
        self.app = web.make_app()
        self.tracer = get_dummy_tracer()
        trace_app(self.app, self.tracer)
        return self.app

    def tearDown(self):
        super(TestAsyncConcurrency, self).tearDown()
        # reset the application if traced
        untrace_app(self.app)

    @gen_test
    def test_concurrent_requests(self):
        # the application must handle concurrent calls
        def make_requests():
            # use a blocking HTTP client (we're in another thread)
            http_client = httpclient.HTTPClient()
            url = self.get_url('/nested/')
            response = http_client.fetch(url)
            eq_(200, response.code)
            eq_('OK', response.body.decode('utf-8'))

        # blocking call executed in different threads
        threads = [threading.Thread(target=make_requests) for _ in range(50)]
        for t in threads:
            t.daemon = True
            t.start()

        # wait for the execution; assuming this time as a timeout
        yield web.compat.sleep(0.2)

        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(50, len(traces))
        eq_(2, len(traces[0]))


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

    def test_arbitrary_resource_querystring(self):
        # users inputs should not determine `span.resource` field
        trace_app(self.app, self.tracer)
        response = self.fetch('/success/?magic_number=42')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('SuccessHandler', request_span.resource)
        eq_('/success/?magic_number=42', request_span.get_tag('http.url'))

    def test_arbitrary_resource_404(self):
        # users inputs should not determine `span.resource` field
        trace_app(self.app, self.tracer)
        response = self.fetch('/does_not_exist/')
        eq_(404, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        request_span = traces[0][0]
        eq_('TracerErrorHandler', request_span.resource)
        eq_('/does_not_exist/', request_span.get_tag('http.url'))


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
