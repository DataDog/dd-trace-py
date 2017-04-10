import time
import unittest

from nose.tools import eq_, ok_

from tornado import version_info

from .utils import TornadoTestCase


class TestTornadoExecutor(TornadoTestCase):
    """
    Ensure that Tornado web handlers are properly traced even if
    ``@run_on_executor`` decorator is used.
    """
    def test_on_executor_handler(self):
        # it should trace a handler that uses @run_on_executor
        response = self.fetch('/executor_handler/')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(2, len(traces))
        eq_(1, len(traces[0]))
        eq_(1, len(traces[1]))

        # this trace yields the execution of the thread
        request_span = traces[1][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.ExecutorHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/executor_handler/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)
        ok_(request_span.duration >= 0.05)

        # this trace is executed in a different thread
        executor_span = traces[0][0]
        eq_('tornado-web', executor_span.service)
        eq_('tornado.executor.with', executor_span.name)
        eq_(executor_span.parent_id, request_span.span_id)
        eq_(0, executor_span.error)
        ok_(executor_span.duration >= 0.05)

    def test_on_delayed_executor_handler(self):
        # it should trace a handler that uses @run_on_executor but that doesn't
        # wait for its termination
        response = self.fetch('/executor_delayed_handler/')
        eq_(200, response.code)

        # timeout for the background thread execution
        time.sleep(0.1)

        traces = self.tracer.writer.pop_traces()
        eq_(2, len(traces))
        eq_(1, len(traces[0]))
        eq_(1, len(traces[1]))

        # order the `traces` list to have deterministic results
        # (required only for this special use case)
        traces.sort(key=lambda x: x[0].name, reverse=True)

        # this trace yields the execution of the thread
        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.ExecutorDelayedHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/executor_delayed_handler/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)

        # this trace is executed in a different thread
        executor_span = traces[1][0]
        eq_('tornado-web', executor_span.service)
        eq_('tornado.executor.with', executor_span.name)
        eq_(executor_span.parent_id, request_span.span_id)
        eq_(0, executor_span.error)
        ok_(executor_span.duration >= 0.05)

    def test_on_executor_exception_handler(self):
        # it should trace a handler that uses @run_on_executor
        response = self.fetch('/executor_exception/')
        eq_(500, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(2, len(traces))
        eq_(1, len(traces[0]))
        eq_(1, len(traces[1]))

        # this trace yields the execution of the thread
        request_span = traces[1][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.ExecutorExceptionHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('500', request_span.get_tag('http.status_code'))
        eq_('/executor_exception/', request_span.get_tag('http.url'))
        eq_(1, request_span.error)
        eq_('Ouch!', request_span.get_tag('error.msg'))
        ok_('Exception: Ouch!' in request_span.get_tag('error.stack'))

        # this trace is executed in a different thread
        executor_span = traces[0][0]
        eq_('tornado-web', executor_span.service)
        eq_('tornado.executor.with', executor_span.name)
        eq_(executor_span.parent_id, request_span.span_id)
        eq_(1, executor_span.error)
        eq_('Ouch!', executor_span.get_tag('error.msg'))
        ok_('Exception: Ouch!' in executor_span.get_tag('error.stack'))

    @unittest.skipIf(
        (version_info[0], version_info[1]) in [(4, 0), (4, 1)],
        reason='Custom kwargs are available only for Tornado 4.2+',
    )
    def test_on_executor_custom_kwarg(self):
        # it should trace a handler that uses @run_on_executor
        # with the `executor` kwarg
        response = self.fetch('/executor_custom_handler/')
        eq_(200, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(2, len(traces))
        eq_(1, len(traces[0]))
        eq_(1, len(traces[1]))

        # this trace yields the execution of the thread
        request_span = traces[1][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.ExecutorCustomHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/executor_custom_handler/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)
        ok_(request_span.duration >= 0.05)

        # this trace is executed in a different thread
        executor_span = traces[0][0]
        eq_('tornado-web', executor_span.service)
        eq_('tornado.executor.with', executor_span.name)
        eq_(executor_span.parent_id, request_span.span_id)
        eq_(0, executor_span.error)
        ok_(executor_span.duration >= 0.05)

    @unittest.skipIf(
        (version_info[0], version_info[1]) in [(4, 0), (4, 1)],
        reason='Custom kwargs are available only for Tornado 4.2+',
    )
    def test_on_executor_custom_args_kwarg(self):
        # it should raise an exception if the decorator is used improperly
        response = self.fetch('/executor_custom_args_handler/')
        eq_(500, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        # this trace yields the execution of the thread
        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.ExecutorCustomArgsHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('500', request_span.get_tag('http.status_code'))
        eq_('/executor_custom_args_handler/', request_span.get_tag('http.url'))
        eq_(1, request_span.error)
        eq_('cannot combine positional and keyword args', request_span.get_tag('error.msg'))
        ok_('ValueError' in request_span.get_tag('error.stack'))
