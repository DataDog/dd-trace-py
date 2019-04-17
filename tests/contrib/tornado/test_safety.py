import threading

from tornado import httpclient
from tornado.testing import gen_test

from ddtrace.contrib.tornado import patch, unpatch

from . import web
from .web.app import CustomDefaultHandler
from .utils import TornadoTestCase


class TestAsyncConcurrency(TornadoTestCase):
    """
    Ensure that application instrumentation doesn't break asynchronous concurrency.
    """
    @gen_test
    def test_concurrent_requests(self):
        # the application must handle concurrent calls
        def make_requests():
            # use a blocking HTTP client (we're in another thread)
            http_client = httpclient.HTTPClient()
            url = self.get_url('/nested/')
            response = http_client.fetch(url)
            assert 200 == response.code
            assert 'OK' == response.body.decode('utf-8')
            # freeing file descriptors
            http_client.close()

        # blocking call executed in different threads
        threads = [threading.Thread(target=make_requests) for _ in range(25)]
        for t in threads:
            t.daemon = True
            t.start()

        # wait for the execution; assuming this time as a timeout
        yield web.compat.sleep(0.5)

        # the trace is created
        traces = self.tracer.writer.pop_traces()
        assert 25 == len(traces)
        assert 2 == len(traces[0])


class TestAppSafety(TornadoTestCase):
    """
    Ensure that the application patch has the proper safety guards.
    """

    def test_trace_unpatch(self):
        # the application must not be traced if unpatch() is called
        patch()
        unpatch()

        response = self.fetch('/success/')
        assert 200 == response.code

        traces = self.tracer.writer.pop_traces()
        assert 0 == len(traces)

    def test_trace_unpatch_not_traced(self):
        # the untrace must be safe if the app is not traced
        unpatch()
        unpatch()

        response = self.fetch('/success/')
        assert 200 == response.code

        traces = self.tracer.writer.pop_traces()
        assert 0 == len(traces)

    def test_trace_app_twice(self):
        # the application must not be traced multiple times
        patch()
        patch()

        response = self.fetch('/success/')
        assert 200 == response.code

        traces = self.tracer.writer.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

    def test_arbitrary_resource_querystring(self):
        # users inputs should not determine `span.resource` field
        response = self.fetch('/success/?magic_number=42')
        assert 200 == response.code

        traces = self.tracer.writer.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert 'tests.contrib.tornado.web.app.SuccessHandler' == request_span.resource
        assert '/success/?magic_number=42' == request_span.get_tag('http.url')

    def test_arbitrary_resource_404(self):
        # users inputs should not determine `span.resource` field
        response = self.fetch('/does_not_exist/')
        assert 404 == response.code

        traces = self.tracer.writer.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert 'tornado.web.ErrorHandler' == request_span.resource
        assert '/does_not_exist/' == request_span.get_tag('http.url')

    @gen_test
    def test_futures_without_context(self):
        # ensures that if futures propagation is available, an empty
        # context doesn't crash the system
        from .web.compat import ThreadPoolExecutor

        def job():
            with self.tracer.trace('job'):
                return 42

        executor = ThreadPoolExecutor(max_workers=3)
        yield executor.submit(job)

        traces = self.tracer.writer.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        # this trace yields the execution of the thread
        span = traces[0][0]
        assert 'job' == span.name


class TestCustomAppSafety(TornadoTestCase):
    """
    Ensure that the application patch has the proper safety guards,
    even for custom default handlers.
    """
    def get_settings(self):
        return {
            'default_handler_class': CustomDefaultHandler,
            'default_handler_args': dict(status_code=400),
        }

    def test_trace_unpatch(self):
        # the application must not be traced if unpatch() is called
        unpatch()

        response = self.fetch('/custom_handler/')
        assert 400 == response.code

        traces = self.tracer.writer.pop_traces()
        assert 0 == len(traces)
