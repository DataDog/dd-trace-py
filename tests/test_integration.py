import os

from unittest import TestCase, skipUnless
from nose.tools import eq_, ok_

from ddtrace.span import Span
from ddtrace.api import API


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
