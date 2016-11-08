import os
import json
import mock
import time

from unittest import TestCase, skipUnless
from nose.tools import eq_, ok_

from ddtrace.api import API
from ddtrace.span import Span
from ddtrace.tracer import Tracer


@skipUnless(
    os.environ.get('TEST_DATADOG_INTEGRATION', False),
    'You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable'
)
class TestWorkers(TestCase):
    """
    Ensures that a workers interacts correctly with the main thread. These are part
    of integration tests so real calls are triggered.
    """
    def setUp(self):
        """
        Create a tracer with running workers, while spying the ``_put()`` method to
        keep trace of triggered API calls.
        """
        # create a new tracer
        self.tracer = Tracer()
        # spy the send() method
        self.api = self.tracer.writer.api
        self.api._put = mock.Mock(self.api._put, wraps=self.api._put)

    def tearDown(self):
        """
        Stop running worker
        """
        self.tracer.writer._worker.stop()

    def _wait_thread_flush(self):
        """
        Helper that waits for the thread flush
        """
        self.tracer.writer._worker.stop()
        self.tracer.writer._worker.join()

    def test_worker_single_trace(self):
        # create a trace block and send it using the transport system
        tracer = self.tracer
        tracer.trace('client.testing').finish()

        # one send is expected
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 1)
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = json.loads(self.api._put.call_args[0][1])
        eq_(endpoint, '/spans')
        eq_(len(payload), 1)
        eq_(payload[0]['name'], 'client.testing')

    def test_worker_multiple_traces(self):
        # make a single send() if multiple traces are created before the flush interval
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        tracer.trace('client.testing').finish()

        # one send is expected
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 1)
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = json.loads(self.api._put.call_args[0][1])
        eq_(endpoint, '/spans')
        eq_(len(payload), 2)
        eq_(payload[0]['name'], 'client.testing')
        eq_(payload[1]['name'], 'client.testing')

    def test_worker_single_trace_multiple_spans(self):
        # make a single send() if a single trace with multiple spans is created before the flush
        tracer = self.tracer
        parent = tracer.trace('client.testing')
        child = tracer.trace('client.testing').finish()
        parent.finish()

        # one send is expected
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 1)
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = json.loads(self.api._put.call_args[0][1])
        eq_(endpoint, '/spans')
        eq_(len(payload), 2)
        eq_(payload[0]['name'], 'client.testing')
        eq_(payload[1]['name'], 'client.testing')

    def test_worker_single_service(self):
        # service must be sent correctly
        tracer = self.tracer
        tracer.set_service_info('client.service', 'django', 'web')
        tracer.trace('client.testing').finish()

        # expect a call for traces and services
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 2)
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = json.loads(self.api._put.call_args[0][1])
        eq_(endpoint, '/services')
        eq_(len(payload.keys()), 1)
        eq_(payload['client.service'], {'app': 'django', 'app_type': 'web'})

    def test_worker_service_called_multiple_times(self):
        # service must be sent correctly
        tracer = self.tracer
        tracer.set_service_info('backend', 'django', 'web')
        tracer.set_service_info('database', 'postgres', 'db')
        tracer.trace('client.testing').finish()

        # expect a call for traces and services
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 2)
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = json.loads(self.api._put.call_args[0][1])
        eq_(endpoint, '/services')
        eq_(len(payload.keys()), 2)
        eq_(payload['backend'], {'app': 'django', 'app_type': 'web'})
        eq_(payload['database'], {'app': 'postgres', 'app_type': 'db'})


@skipUnless(
    os.environ.get('TEST_DATADOG_INTEGRATION', False),
    'You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable'
)
class TestAPITransport(TestCase):
    """
    Ensures that traces are properly sent to a local agent. These are part
    of integration tests so real calls are triggered and you have to execute
    a real trace-agent to let them pass.
    """
    def setUp(self):
        """
        Create a tracer without workers, while spying the ``send()`` method
        """
        # create a new API object to test the transport using synchronous calls
        self.api = API('localhost', 7777, wait_response=True)

    def test_send_single_trace(self):
        # register a single trace with a span and send them to the trace agent
        traces = [
            [Span(name='client.testing', tracer=None)],
        ]

        response = self.api.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_multiple_traces(self):
        # register some traces and send them to the trace agent
        traces = [
            [Span(name='client.testing', tracer=None)],
            [Span(name='client.testing', tracer=None)],
        ]

        response = self.api.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_single_trace_multiple_spans(self):
        # register some traces and send them to the trace agent
        traces = [
            [Span(name='client.testing', tracer=None), Span(name='client.testing', tracer=None)],
        ]

        response = self.api.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_multiple_traces_multiple_spans(self):
        # register some traces and send them to the trace agent
        traces = [
            [Span(name='client.testing', tracer=None), Span(name='client.testing', tracer=None)],
            [Span(name='client.testing', tracer=None), Span(name='client.testing', tracer=None)],
        ]

        response = self.api.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_single_service(self):
        # register some services and send them to the trace agent
        services = [{
            'client.service': {
                'app': 'django',
                'app_type': 'web',
            },
        }]

        response = self.api.send_services(services)
        ok_(response)
        eq_(response.status, 200)

    def test_send_service_called_multiple_times(self):
        # register some services and send them to the trace agent
        services = [{
            'backend': {
                'app': 'django',
                'app_type': 'web',
            },
            'database': {
                'app': 'postgres',
                'app_type': 'db',
            },
        }]

        response = self.api.send_services(services)
        ok_(response)
        eq_(response.status, 200)
